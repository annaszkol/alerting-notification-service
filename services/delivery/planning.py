"""plan_notifications: turns a new alert into outbox rows (spec §6, contract: contracts/delivery.md).

Runs inside the processor's batch transaction. It never commits and never sends anything.

- recipients: `user`, `team`, `{team, role}`, `oncall` (person on duty now; whole team if no rotation);
  unknown users and teams resolve to nobody,
- channels: a list from the rule as is; a schedule from the rule in the recipient's time zone; `preferences`
  (or omitted) from the person's preferences for the alert severity, else team defaults, else built-in defaults,
- one notification per (person, channel, first event), even if several route entries or rules hit that person,
- every alert notification carries the person's `ACK <code>` (one per alert and person, contracts/delivery.md),
- throttling per person and channel (limits from preferences, see schedule.DEFAULT_LIMITS): under the limit
  `pending`; the first time over it one `kind = limit` message and the alert `held`; afterwards `held`.
  Critical alerts by phone bypass the limit.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import psycopg

from contracts.delivery import (
    AlertForPlanning,
    Channel,
    NotificationKind,
    NotificationStatus,
    PlannedNotification,
    PlanResult,
    RuleForPlanning,
    Severity,
)
from db.rows import plain_rows
from services.delivery.codes import new_code
from services.delivery.directory import Directory, Profile, SqlDirectory
from services.delivery.schedule import DEFAULT_LIMITS, Limit, pick_channels, preference_for, zone


def resolve_recipients(
    conn: psycopg.Connection, directory: Directory, to: Mapping[str, str], now: datetime
) -> list[str]:
    if "user" in to:
        return directory.existing_users(conn, [to["user"]])
    if "oncall" in to:
        return directory.on_call(conn, to["oncall"], now)
    if "team" in to:
        return directory.team_members(conn, to["team"], to.get("role"))
    raise ValueError(f"unknown recipient {dict(to)!r}")


def channels_for(spec: Any, profile: Profile | None, severity: Severity, now: datetime) -> list[Channel]:
    """Channels for one person. A channel list or schedule from the rule wins over the person's preferences."""
    if isinstance(spec, list):
        return [Channel(c) for c in spec]
    if profile is None:  # unknown user; existing_users already filtered them, so this is defensive
        return []
    local = now.astimezone(zone(profile.timezone))
    if isinstance(spec, Mapping):  # schedule from the rule
        return pick_channels(spec, local)
    return pick_channels(preference_for(profile.by_severity, severity), local)


@plain_rows
def plan_targets(
    conn: psycopg.Connection,
    directory: Directory,
    route: Iterable[Mapping[str, Any]],
    now: datetime,
    severity: Severity = Severity.CRITICAL,
    owner_team: str | None = None,
) -> list[tuple[str, Channel]]:
    """Unique (user, channel) pairs for a route, sorted so that row locks are taken in a fixed order."""
    entries = [
        (resolve_recipients(conn, directory, entry["to"], now), entry.get("channels", "preferences")) for entry in route
    ]
    need_profile = {user for users, spec in entries if not isinstance(spec, list) for user in users}
    profiles = directory.profiles(conn, need_profile, owner_team) if need_profile else {}
    targets: set[tuple[str, Channel]] = set()
    for users, spec in entries:
        for user in users:
            targets.update((user, channel) for channel in channels_for(spec, profiles.get(user), severity, now))
    return sorted(targets)


INSERT_SQL = """
INSERT INTO notifications (alert_id, user_id, channel, kind, dedup_key, status, next_attempt_at, created_at, reply_code)
VALUES (%(alert_id)s, %(user_id)s, %(channel)s, %(kind)s, %(dedup_key)s, %(status)s, %(now)s, %(now)s, %(reply_code)s)
ON CONFLICT (user_id, channel, dedup_key) DO NOTHING
RETURNING id, user_id, channel, kind, status
"""

# Namespace for advisory locks taken by throttling (two-int form: (LOCK_CLASS, hashtext(user:channel))).
LOCK_CLASS = 72_110

EXISTS_SQL = "SELECT 1 FROM notifications WHERE user_id = %s AND channel = %s AND dedup_key = %s"

SENT_IN_WINDOW_SQL = """
SELECT count(*) FROM notifications
WHERE user_id = %s AND channel = %s AND kind = 'alert' AND status <> 'held' AND created_at > %s
"""

LIMIT_IN_WINDOW_SQL = """
SELECT 1 FROM notifications WHERE user_id = %s AND channel = %s AND kind = 'limit' AND created_at > %s LIMIT 1
"""


def ack_codes(conn: psycopg.Connection, alert_id: int, users: list[str]) -> dict[str, str]:
    """One `ACK <code>` per (alert, person), shared by all of that person's channels (contracts/delivery.md).
    A person who already has a code for this alert (another rule, an escalation) keeps it."""
    existing = dict(
        conn.execute(
            "SELECT DISTINCT ON (user_id) user_id, reply_code FROM notifications"
            " WHERE alert_id = %s AND user_id = ANY(%s) AND kind = 'alert' AND reply_code IS NOT NULL"
            " ORDER BY user_id, id",
            (alert_id, users),
        ).fetchall()
    )
    return {user: existing.get(user) or new_code() for user in users}


def bypasses_limit(channel: Channel, severity: Severity) -> bool:
    """Spec §14 default: critical alerts by phone are never throttled."""
    return channel is Channel.PHONE and severity is Severity.CRITICAL


@dataclass(frozen=True)
class NotificationPlanner:
    """Implements contracts.delivery.PlanNotifications."""

    directory: Directory = field(default_factory=SqlDirectory)

    @plain_rows
    def __call__(
        self,
        conn: psycopg.Connection,
        alert: AlertForPlanning,
        rule: RuleForPlanning,
        now: datetime,
    ) -> PlanResult:
        severity, owner_team = Severity(alert.severity), rule.owner.get("team")
        targets = plan_targets(conn, self.directory, rule.route, now, severity, owner_team)
        if not targets:
            return PlanResult()
        profiles = self.directory.profiles(conn, {user for user, _ in targets}, owner_team)
        codes = ack_codes(conn, alert.id, sorted({user for user, _ in targets}))
        rows: list[tuple[Any, ...]] = []
        for user, channel in targets:  # sorted: advisory locks are always taken in the same order
            profile = profiles.get(user)
            limits = profile.limits if profile else DEFAULT_LIMITS
            limit = limits.get(channel.value)
            code = codes[user]
            if limit is None or bypasses_limit(channel, severity):
                rows += self._insert(
                    conn, alert, user, channel, NotificationKind.ALERT, alert.first_event_id, now, reply_code=code
                )
            else:
                rows += self._throttled(conn, alert, user, channel, limit, now, code)
        return PlanResult(notifications=_planned(rows))

    def _throttled(
        self,
        conn: psycopg.Connection,
        alert: AlertForPlanning,
        user: str,
        channel: Channel,
        limit: Limit,
        now: datetime,
        code: str | None = None,
    ) -> list[tuple[Any, ...]]:
        """Spec §6 step 4. The transaction-scoped advisory lock serialises every planner (in any processor
        instance) that wants to message this person on this channel, so the count below is exact."""
        conn.execute("SELECT pg_advisory_xact_lock(%s::int, hashtext(%s))", (LOCK_CLASS, f"{user}:{channel.value}"))
        if conn.execute(EXISTS_SQL, (user, channel.value, alert.first_event_id)).fetchone():
            return []  # already planned for this event (another rule, or a replay)
        since = now - limit.window
        sent = conn.execute(SENT_IN_WINDOW_SQL, (user, channel.value, since)).fetchone()[0]
        if sent < limit.max:
            return self._insert(
                conn, alert, user, channel, NotificationKind.ALERT, alert.first_event_id, now, reply_code=code
            )
        rows: list[tuple[Any, ...]] = []
        if not conn.execute(LIMIT_IN_WINDOW_SQL, (user, channel.value, since)).fetchone():
            rows += self._insert(conn, alert, user, channel, NotificationKind.LIMIT, f"limit:{now.isoformat()}", now)
        rows += self._insert(
            conn,
            alert,
            user,
            channel,
            NotificationKind.ALERT,
            alert.first_event_id,
            now,
            NotificationStatus.HELD,
            reply_code=code,
        )
        return rows

    @staticmethod
    def _insert(
        conn: psycopg.Connection,
        alert: AlertForPlanning,
        user: str,
        channel: Channel,
        kind: NotificationKind,
        dedup_key: str,
        now: datetime,
        status: NotificationStatus = NotificationStatus.PENDING,
        reply_code: str | None = None,
    ) -> list[tuple[Any, ...]]:
        return conn.execute(
            INSERT_SQL,
            {
                "alert_id": alert.id,
                "user_id": user,
                "channel": channel.value,
                "kind": kind.value,
                "dedup_key": dedup_key,
                "status": status.value,
                "now": now,
                "reply_code": reply_code,
            },
        ).fetchall()


def _planned(rows: Sequence[tuple[Any, ...]]) -> list[PlannedNotification]:
    return [
        PlannedNotification(
            id=row_id,
            user_id=user_id,
            channel=Channel(channel),
            kind=NotificationKind(kind),
            status=NotificationStatus(status),
        )
        for row_id, user_id, channel, kind, status in sorted(rows, key=lambda r: (r[1], r[2], r[3] != "limit"))
    ]


# Default instance for the processor: users and teams from the database.
plan_notifications = NotificationPlanner()
