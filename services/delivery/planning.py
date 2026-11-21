"""plan_notifications: turns a new alert into outbox rows (spec §6, contract: contracts/delivery.md).

Runs inside the processor's batch transaction. It never commits and never sends anything.

- recipients: `user`, `team`, `{team, role}`, `oncall` (person on duty now; whole team if no rotation);
  unknown users and teams resolve to nobody,
- channels: a list from the rule as is; a schedule from the rule in the recipient's time zone; `preferences`
  (or omitted) from the person's preferences for the alert severity, else team defaults, else built-in defaults,
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
    Severity,
)
from services.delivery.directory import Directory, Profile, SqlDirectory
from services.delivery.schedule import pick_channels, preference_for, zone


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
        targets = plan_targets(conn, self.directory, rule.route, now, Severity(alert.severity), rule.owner.get("team"))
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
