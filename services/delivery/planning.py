"""plan_notifications: turns a new alert into outbox rows (spec §6, contract: contracts/delivery.md).

Runs inside the processor's batch transaction. It never commits and never sends anything.

Scope of this version (feat/notifications-outbox):
- recipients: `user`, `team`, `{team, role}`; `oncall` resolves to every team member until rotations land
  (feat/preferences-oncall); unknown users and teams resolve to nobody,
- channels: an explicit list from the rule; `schedule` and `preferences` fall back to `ui` (stub),
- one notification per (person, channel, first event), even if several route entries or rules hit that person,
- no throttling yet (feat/throttling): every row is `pending`.
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
)
from services.delivery.directory import Directory, SqlDirectory

STUB_CHANNELS: tuple[Channel, ...] = (Channel.UI,)


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


def resolve_channels(channels: Any) -> tuple[Channel, ...]:
    if isinstance(channels, list):
        return tuple(Channel(c) for c in channels)
    # "preferences", omitted, or a schedule: real resolution lands in feat/preferences-oncall.
    return STUB_CHANNELS


def plan_targets(
    conn: psycopg.Connection, directory: Directory, route: Iterable[Mapping[str, Any]], now: datetime
) -> list[tuple[str, Channel]]:
    """Unique (user, channel) pairs for a route, sorted so that row locks are taken in a fixed order."""
    targets: set[tuple[str, Channel]] = set()
    for entry in route:
        users = resolve_recipients(conn, directory, entry["to"], now)
        channels = resolve_channels(entry.get("channels", "preferences"))
        targets.update((user, channel) for user in users for channel in channels)
    return sorted(targets)


INSERT_SQL = """
INSERT INTO notifications (alert_id, user_id, channel, kind, dedup_key, status, next_attempt_at)
SELECT %(alert_id)s, t.user_id, t.channel, %(kind)s, %(dedup_key)s, %(status)s, %(now)s
FROM unnest(%(user_ids)s::text[], %(channels)s::text[]) WITH ORDINALITY AS t(user_id, channel, ord)
ORDER BY t.ord
ON CONFLICT (user_id, channel, dedup_key) DO NOTHING
RETURNING id, user_id, channel, kind, status
"""


@dataclass(frozen=True)
class NotificationPlanner:
    """Implements contracts.delivery.PlanNotifications."""

    directory: Directory = field(default_factory=SqlDirectory)

    def __call__(
        self,
        conn: psycopg.Connection,
        alert: AlertForPlanning,
        rule: RuleForPlanning,
        now: datetime,
    ) -> PlanResult:
        targets = plan_targets(conn, self.directory, rule.route, now)
        if not targets:
            return PlanResult()
        rows = conn.execute(
            INSERT_SQL,
            {
                "alert_id": alert.id,
                "kind": NotificationKind.ALERT.value,
                "dedup_key": alert.first_event_id,
                "status": NotificationStatus.PENDING.value,
                "now": now,
                "user_ids": [user for user, _ in targets],
                "channels": [channel.value for _, channel in targets],
            },
        ).fetchall()
        return PlanResult(notifications=_planned(rows))


def _planned(rows: Sequence[tuple[Any, ...]]) -> list[PlannedNotification]:
    return [
        PlannedNotification(
            id=row_id,
            user_id=user_id,
            channel=Channel(channel),
            kind=NotificationKind(kind),
            status=NotificationStatus(status),
        )
        for row_id, user_id, channel, kind, status in sorted(rows, key=lambda r: (r[1], r[2]))
    ]


# Default instance for the processor: users and teams from the database.
plan_notifications = NotificationPlanner()
