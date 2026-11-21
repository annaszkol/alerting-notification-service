"""One batch of events in ONE transaction (spec §5 "Algorytm dla jednej paczki", AGENTS.md invariants).

1. `processed_events` first; event ids already there are skipped (replays and retries are no-ops).
2. In memory: mapping -> letters, rule matching, fingerprints. Firing letters are grouped per fingerprint:
   count `n`, first letter, latest `$eventTime`.
3. Per fingerprint, in sorted order (fixed lock order): `INSERT ... ON CONFLICT (fingerprint) WHERE state <>
   'resolved' DO NOTHING RETURNING id`. A new row -> notifications (`plan_notifications`), auto runbook runs and
   the audit entry, all in this transaction. No row -> `UPDATE ... count = count + n`; if that hits nothing (the
   alert was resolved meanwhile) -> back to the INSERT, at most 3 times.
4. Resolved letters: `UPDATE alerts SET state = 'resolved', resolved_how = 'event' WHERE correlation_key = ... AND
   last_event_time <= <event time>`, so the order of events in a batch does not matter.
5. `NOTIFY alerts_changed` with the ids. The caller commits, then commits the Kafka offset.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from contracts.delivery import AlertForPlanning, RuleForPlanning, Severity
from contracts.notify import notify_alerts_changed
from libs.conditions import MISSING, Envelope, evaluate, get_path, map_event, render, shape_hash
from services.delivery.planning import plan_notifications
from services.delivery.schedule import parse_window
from services.processor.config import Config, Rule

log = logging.getLogger("processor")

MAX_INSERT_RETRIES = 3
SEVERITIES = {s.value for s in Severity}


@dataclass
class Group:
    rule: Rule
    fingerprint: str
    first: dict[str, Any]
    last: dict[str, Any]
    last_time: datetime
    n: int = 1


@dataclass
class BatchResult:
    new_events: int = 0
    duplicate_events: int = 0
    letters: int = 0
    unmapped: int = 0
    created: list[int] = field(default_factory=list)
    updated: list[int] = field(default_factory=list)
    resolved: list[int] = field(default_factory=list)

    @property
    def changed(self) -> list[int]:
        return sorted({*self.created, *self.updated, *self.resolved})


def event_time(letter: dict[str, Any]) -> datetime:
    for key in ("$eventTime", "$receivedAt"):
        try:
            parsed = datetime.fromisoformat(str(letter.get(key)).replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            continue
    return datetime.now(UTC)


def _plain(value: Any) -> Any:
    return None if value is MISSING else value


def fingerprint(rule: Rule, letter: dict[str, Any]) -> str:
    """hash(rule id, dedup.key values) (spec §5). Default key: `$correlationKey`, else `$service` + `$summary`.
    Mode `window` adds the clock-aligned window start, so every instance computes the same value."""
    keys = rule.dedup.get("key")
    if keys:
        values = [_plain(get_path(letter, k)) for k in keys]
    elif letter.get("$correlationKey"):
        values = [letter["$correlationKey"]]
    else:
        values = [letter.get("$service"), letter.get("$summary")]
    window_start = None
    if rule.dedup.get("mode") == "window":
        seconds = int(parse_window(rule.dedup.get("window", "5m")).total_seconds())
        window_start = int(event_time(letter).timestamp()) // seconds * seconds
    raw = json.dumps([rule.id, values, window_start], default=str, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def _exists(conn: psycopg.Connection, table: str) -> bool:
    return conn.execute("SELECT to_regclass(%s) IS NOT NULL", (table,)).fetchone()[0]


# --- the batch -----------------------------------------------------------------------------------------


def process_batch(
    conn: psycopg.Connection, envelopes: Sequence[Envelope], config: Config, now: datetime | None = None
) -> BatchResult:
    """Run inside the caller's transaction; the caller commits it and then the Kafka offsets."""
    now = now or datetime.now(UTC)
    result = BatchResult()
    unique = {e.event_id: e for e in envelopes}  # the same event twice in one batch counts once

    fresh = {
        r[0]
        for r in conn.execute(
            "INSERT INTO processed_events (event_id) SELECT unnest(%s::text[])"
            " ON CONFLICT DO NOTHING RETURNING event_id",
            (list(unique),),
        ).fetchall()
    }
    result.new_events = len(fresh)
    result.duplicate_events = len(envelopes) - len(fresh)

    groups: dict[tuple[str, str], Group] = {}
    resolved: list[dict[str, Any]] = []
    unmapped: list[tuple[str, Any]] = []
    for event_id in sorted(fresh):  # deterministic
        envelope = unique[event_id]
        try:
            mapped = map_event(envelope, config.source(envelope.source_id), config.mappings)
        except Exception:  # noqa: BLE001 - a broken payload must not stop the batch
            log.exception("event %s: mapping failed", event_id)
            unmapped.append((envelope.source_id, envelope.payload))
            continue
        for item in mapped:
            letter = item.letter
            result.letters += 1
            if item.mapping is None:
                unmapped.append((envelope.source_id, letter["payload"]))
            if letter["$status"] == "resolved":
                resolved.append(letter)
                continue
            for rule in config.rules:
                if not evaluate(rule.when, letter):
                    continue
                fp = fingerprint(rule, letter)
                when = event_time(letter)
                group = groups.get((rule.id, fp))
                if group is None:
                    groups[(rule.id, fp)] = Group(rule, fp, letter, letter, when)
                else:
                    group.n += 1
                    if when >= group.last_time:
                        group.last, group.last_time = letter, when

    for key in sorted(groups):  # fixed order of row locks
        _upsert_alert(conn, groups[key], now, result)
    for letter in resolved:
        _resolve_by_event(conn, letter, now, result)
    _store_unmapped(conn, unmapped, now)
    result.unmapped = len(unmapped)
    notify_alerts_changed(conn, result.changed)
    return result


INSERT_ALERT = """
INSERT INTO alerts (rule_id, owner, fingerprint, correlation_key, state, severity, title, body, color, fields,
                    count, first_seen, last_seen, last_event_time, first_event, last_event, auto_resolve_after,
                    previous_alert_id)
VALUES (%(rule_id)s, %(owner)s, %(fingerprint)s, %(correlation_key)s, 'open', %(severity)s, %(title)s, %(body)s,
        %(color)s, %(fields)s, %(count)s, %(now)s, %(now)s, %(last_time)s, %(first)s, %(last)s, %(auto_resolve_after)s,
        (SELECT max(id) FROM alerts WHERE fingerprint = %(fingerprint)s AND state = 'resolved'))
ON CONFLICT (fingerprint) WHERE state <> 'resolved' DO NOTHING
RETURNING id
"""

UPDATE_ALERT = """
UPDATE alerts SET count = count + %(count)s, last_seen = %(now)s,
                  last_event = CASE WHEN %(last_time)s >= last_event_time THEN %(last)s ELSE last_event END,
                  last_event_time = greatest(last_event_time, %(last_time)s)
WHERE fingerprint = %(fingerprint)s AND state <> 'resolved'
RETURNING id
"""


def _alert_values(group: Group, now: datetime) -> dict[str, Any]:
    present, first = group.rule.present, group.first
    severity = str(present.get("severity") or first.get("$severity") or "info").lower()
    title = render(present.get("title") or "{{$summary}}", first)
    return {
        "rule_id": group.rule.id,
        "owner": Jsonb(dict(group.rule.owner)),
        "fingerprint": group.fingerprint,
        "correlation_key": first.get("$correlationKey"),
        "severity": severity if severity in SEVERITIES else "info",
        "title": title,
        "body": render(present.get("body"), first) if present.get("body") else None,
        "color": present.get("color"),
        "fields": Jsonb(list(present.get("fields") or [])),
        "count": group.n,
        "now": now,
        "last_time": group.last_time,
        "first": Jsonb(first),
        "last": Jsonb(group.last),
        "auto_resolve_after": group.rule.auto_resolve_after,
    }


def _upsert_alert(conn: psycopg.Connection, group: Group, now: datetime, result: BatchResult) -> None:
    values = _alert_values(group, now)
    for _ in range(MAX_INSERT_RETRIES):
        row = conn.execute(INSERT_ALERT, values).fetchone()
        if row:
            alert_id = row[0]
            result.created.append(alert_id)
            _on_new_alert(conn, alert_id, group, values["severity"], now)
            return
        row = conn.execute(UPDATE_ALERT, values).fetchone()
        if row:
            result.updated.append(row[0])
            return
    log.warning("fingerprint %s: alert kept changing state, giving up for this batch", group.fingerprint)


def _on_new_alert(conn: psycopg.Connection, alert_id: int, group: Group, severity: str, now: datetime) -> None:
    rule = group.rule
    plan_notifications(
        conn,
        AlertForPlanning(
            id=alert_id, rule_id=rule.id, severity=Severity(severity), first_event_id=group.first["$eventId"]
        ),
        RuleForPlanning(id=rule.id, owner=dict(rule.owner), route=list(rule.route)),
        now,
    )
    conn.execute(
        "INSERT INTO audit_log (actor, action, target_type, target_id, details) VALUES ('processor', 'alert.created',"
        " 'alert', %s, %s)",
        (str(alert_id), Jsonb({"rule": rule.id, "count": group.n})),
    )
    _start_auto_runbooks(conn, alert_id, rule, now)


def _start_auto_runbooks(conn: psycopg.Connection, alert_id: int, rule: Rule, now: datetime) -> None:
    """Approved runbooks in mode `auto` start with the alert (spec §7); manual ones wait for the "Start run" button."""
    if not rule.runbook_ids or not _exists(conn, "runbooks"):
        return
    from services.executor.engine import Executor

    with conn.cursor(row_factory=dict_row) as cur:
        rows = cur.execute(
            "SELECT id, canonical FROM runbooks WHERE id = ANY(%s) AND mode = 'auto' AND approved_by IS NOT NULL"
            " ORDER BY id",
            (list(rule.runbook_ids),),
        ).fetchall()
    for row in rows:
        Executor(actions={}).start_run(conn, alert_id, row["canonical"], now, started_by=None, mode="auto")


def _resolve_by_event(conn: psycopg.Connection, letter: dict[str, Any], now: datetime, result: BatchResult) -> None:
    key = letter.get("$correlationKey")
    if not key:
        return  # no correlation key: nothing to close (spec §3)
    rows = conn.execute(
        """UPDATE alerts SET state = 'resolved', resolved_how = 'event', resolved_by = 'processor', resolved_at = %s
           WHERE correlation_key = %s AND state <> 'resolved' AND last_event_time <= %s
           RETURNING id""",
        (now, key, event_time(letter)),
    ).fetchall()
    for (alert_id,) in rows:
        result.resolved.append(alert_id)
        conn.execute(
            "INSERT INTO audit_log (actor, action, target_type, target_id, details) VALUES ('processor',"
            " 'alert.resolved', 'alert', %s, %s)",
            (str(alert_id), Jsonb({"how": "event", "event": letter["$eventId"]})),
        )


def _store_unmapped(conn: psycopg.Connection, samples: Iterable[tuple[str, Any]], now: datetime) -> None:
    """One row per (shape, source) with a count, for the "Niezmapowane" view (spec §3)."""
    samples = list(samples)
    if not samples or not _exists(conn, "unmapped_samples"):
        return
    for source_id, payload in samples:
        shape = shape_hash(payload)
        updated = conn.execute(
            "UPDATE unmapped_samples SET count = count + 1, last_seen = %s, sample = %s"
            " WHERE shape_hash = %s AND source_id = %s",
            (now, Jsonb(payload), shape, source_id),
        ).rowcount
        if not updated:
            conn.execute(
                "INSERT INTO unmapped_samples (shape_hash, source_id, sample, count, last_seen)"
                " VALUES (%s, %s, %s, 1, %s)",
                (shape, source_id, Jsonb(payload), now),
            )
