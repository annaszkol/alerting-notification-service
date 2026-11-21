"""Delivery worker (spec §6 "Wysyłka"): claim -> render -> send -> record.

- Claim: one UPDATE ... WHERE id IN (SELECT ... FOR UPDATE SKIP LOCKED) RETURNING, committed at once, so any
  number of workers can run side by side. A row whose worker died comes back when its lease expires.
- Render at send time: live fields ({{$count}}, {{$state}}, {{$alertUrl}}) are current.
- Send concurrently through the channel adapters (services/delivery/channels).
- Record: sent; retryable -> pending with backoff 5 s, 10 s, 20 s, ... capped at 5 min; permanent error or the
  10th failed attempt -> dead. The update only applies while the row is still ours (status = sending and the
  same attempt number), so a worker whose lease expired cannot overwrite the result of the worker that
  took the row over.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
from psycopg.rows import dict_row

from contracts.delivery import (
    Channel,
    ChannelAdapter,
    Message,
    NotificationKind,
    Recipient,
    SendResult,
    SendStatus,
)
from services.delivery.channels import registry
from services.delivery.messages import fill_live_fields, phone_text, sms_text

log = logging.getLogger("delivery")

LEASE = timedelta(seconds=60)
BATCH_SIZE = 50
MAX_ATTEMPTS = 10
BACKOFF_BASE = timedelta(seconds=5)
BACKOFF_CAP = timedelta(minutes=5)
SEND_CONCURRENCY = 20


def backoff(attempts: int) -> timedelta:
    """Delay after the `attempts`-th failed attempt: 5 s, 10 s, 20 s, ... capped at 5 min."""
    return min(BACKOFF_BASE * (2 ** max(attempts - 1, 0)), BACKOFF_CAP)


CLAIM_SQL = """
UPDATE notifications
SET status = 'sending', lease_until = %(now)s + %(lease)s, attempts = attempts + 1
WHERE id IN (
    SELECT id FROM notifications
    WHERE (status = 'pending' AND next_attempt_at <= %(now)s)
       OR (status = 'sending' AND lease_until < %(now)s)
    ORDER BY next_attempt_at
    LIMIT %(limit)s
    FOR UPDATE SKIP LOCKED
)
RETURNING id, alert_id, user_id, channel, kind, dedup_key, attempts, reply_code
"""

COMPLETE_SQL = """
UPDATE notifications
SET status = %(status)s, lease_until = NULL, next_attempt_at = %(next_attempt_at)s,
    last_error = %(last_error)s, sent_at = %(sent_at)s
WHERE id = %(id)s AND status = 'sending' AND attempts = %(attempts)s
"""

USERS_SQL = "SELECT id, name, email, phone, timezone FROM users WHERE id = ANY(%s)"

# Columns as in spec §8 (table owned by A).
ALERTS_SQL = "SELECT id, title, body, severity, state, count FROM alerts WHERE id = ANY(%s)"

CHANNELS_SQL = "SELECT name, adapter, config FROM channels WHERE enabled"


@dataclass(frozen=True)
class Claimed:
    id: int
    alert_id: int
    user_id: str
    channel: Channel
    kind: NotificationKind
    dedup_key: str
    attempts: int
    reply_code: str | None


@dataclass(frozen=True)
class Outcome:
    status: str  # sent | pending | dead
    next_attempt_at: datetime
    last_error: str | None
    sent_at: datetime | None


def decide(result: SendResult, attempts: int, now: datetime) -> Outcome:
    if result.status is SendStatus.SENT:
        return Outcome("sent", now, None, now)
    if result.status is SendStatus.RETRYABLE_ERROR and attempts < MAX_ATTEMPTS:
        return Outcome("pending", now + backoff(attempts), result.error, None)
    reason = result.error if result.status is SendStatus.PERMANENT_ERROR else f"gave up: {result.error}"
    return Outcome("dead", now, reason, None)


async def claim(
    conn: psycopg.AsyncConnection, now: datetime, limit: int = BATCH_SIZE, lease: timedelta = LEASE
) -> list[Claimed]:
    async with conn.transaction():
        cur = await conn.execute(CLAIM_SQL, {"now": now, "lease": lease, "limit": limit})
        rows = await cur.fetchall()
    return [Claimed(r[0], r[1], r[2], Channel(r[3]), NotificationKind(r[4]), r[5], r[6], r[7]) for r in sorted(rows)]


async def complete(conn: psycopg.AsyncConnection, results: list[tuple[Claimed, Outcome]]) -> int:
    """Store outcomes; returns how many rows were still ours."""
    applied = 0
    async with conn.transaction():
        for row, outcome in results:
            cur = await conn.execute(
                COMPLETE_SQL,
                {
                    "id": row.id,
                    "attempts": row.attempts,
                    "status": outcome.status,
                    "next_attempt_at": outcome.next_attempt_at,
                    "last_error": outcome.last_error,
                    "sent_at": outcome.sent_at,
                },
            )
            applied += cur.rowcount
    return applied


def render(row: Claimed, user: Mapping[str, Any], alert: Mapping[str, Any], base_url: str) -> Message:
    """Build the channel message. Live fields are filled in now (spec §3)."""
    alert_url = f"{base_url}/a/{alert['id']}"
    title = fill_live_fields(alert["title"] or "", alert, alert_url)
    body = fill_live_fields(alert["body"] or "", alert, alert_url)
    severity = alert["severity"] or "info"
    recipient = Recipient(
        user_id=user["id"],
        name=user["name"],
        email=user["email"],
        phone=user["phone"],
        timezone=user["timezone"],
    )

    if row.kind is NotificationKind.LIMIT:
        subject = f"Notification limit reached ({row.channel.value})"
        text = f"Limit reached: further alerts only in the UI. {base_url}/"
    elif row.kind is NotificationKind.APPROVAL:
        subject = f"Approval requested: {title}"
        text = f"Approval requested: {title} {alert_url}" + (
            f" APPROVE {row.reply_code} / REJECT {row.reply_code}" if row.reply_code else ""
        )
    elif row.channel is Channel.SMS:
        subject, text = title, sms_text(severity, title, alert_url, row.reply_code)
    elif row.channel is Channel.PHONE:
        subject, text = title, phone_text(severity, title, can_ack=row.reply_code is not None)
    else:
        subject = title
        text = "\n\n".join(part for part in (body, f"Open the alert: {alert_url}") if part)
        if row.reply_code and row.channel is Channel.EMAIL:
            text += f"\nAcknowledge by SMS: ACK {row.reply_code}"

    if row.channel is Channel.SMS and row.kind is not NotificationKind.ALERT:
        text = text[:160]

    return Message(
        notification_id=row.id,
        alert_id=row.alert_id,
        kind=row.kind,
        channel=row.channel,
        recipient=recipient,
        subject=subject,
        text=text,
        reply_code=row.reply_code,
        links={"alert": alert_url},
    )


class DeliveryWorker:
    def __init__(
        self,
        conn: psycopg.AsyncConnection,
        base_url: str,
        adapters: Mapping[str, ChannelAdapter] | None = None,
        batch_size: int = BATCH_SIZE,
    ) -> None:
        self.conn = conn
        self.base_url = base_url.rstrip("/")
        self.adapters: dict[str, ChannelAdapter] = dict(adapters or {})
        self.batch_size = batch_size
        self.semaphore = asyncio.Semaphore(SEND_CONCURRENCY)

    async def load_adapters(self) -> None:
        """(Re)build adapters from the `channels` table."""
        cur = await self.conn.execute(CHANNELS_SQL)
        fresh: dict[str, ChannelAdapter] = {}
        for name, adapter, config in await cur.fetchall():
            try:
                fresh[name] = registry.create(adapter, config)
            except (ValueError, KeyError) as exc:
                log.error("channel %s: cannot create adapter %s: %s", name, adapter, exc)
        old, self.adapters = self.adapters, fresh
        for adapter in old.values():
            close = getattr(adapter, "aclose", None)
            if close:
                await close()

    async def run_once(self, now: datetime | None = None) -> int:
        """Claim and process one batch. Returns the number of claimed notifications."""
        rows = await claim(self.conn, now or datetime.now(UTC), self.batch_size)
        if not rows:
            return 0
        users = await self._by_id(USERS_SQL, sorted({r.user_id for r in rows}))
        alerts = await self._by_id(ALERTS_SQL, sorted({r.alert_id for r in rows}))
        results = await asyncio.gather(*(self._deliver(row, users, alerts) for row in rows))
        done_at = now or datetime.now(UTC)  # backoff counts from the end of the send
        outcomes = [(row, decide(result, row.attempts, done_at)) for row, result in zip(rows, results, strict=True)]
        await complete(self.conn, outcomes)
        for row, outcome in outcomes:
            if outcome.status != "sent":
                log.warning("notification %s (%s) -> %s: %s", row.id, row.channel, outcome.status, outcome.last_error)
        return len(rows)

    async def _by_id(self, sql: str, ids: list[Any]) -> dict[Any, dict[str, Any]]:
        async with self.conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(sql, (ids,))
            return {row["id"]: row for row in await cur.fetchall()}

    async def _deliver(self, row: Claimed, users: Mapping, alerts: Mapping) -> SendResult:
        user, alert = users.get(row.user_id), alerts.get(row.alert_id)
        if user is None:
            return SendResult(SendStatus.PERMANENT_ERROR, error=f"user {row.user_id} not found")
        if alert is None:
            return SendResult(SendStatus.PERMANENT_ERROR, error=f"alert {row.alert_id} not found")
        adapter = self.adapters.get(row.channel.value)
        if adapter is None:
            error = f"channel {row.channel.value} disabled or unknown"
            return SendResult(SendStatus.RETRYABLE_ERROR, error=error)
        try:
            message = render(row, user, alert, self.base_url)
            async with self.semaphore:
                return await adapter.send(message)
        except Exception as exc:  # an adapter bug must not lose the row: retry it
            log.exception("notification %s: send failed", row.id)
            return SendResult(SendStatus.RETRYABLE_ERROR, error=f"{type(exc).__name__}: {exc}")
