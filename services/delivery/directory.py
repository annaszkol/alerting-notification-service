"""Who is who: users, team membership, on-call rotations, time zones and channel preferences.

Used by plan_notifications inside the processor's transaction, so every lookup is a plain read.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

import psycopg

from services.delivery.schedule import DEFAULT_PREFERENCES, Rotation


@dataclass(frozen=True)
class Profile:
    """What routing needs to know about one person."""

    timezone: str
    by_severity: Mapping[str, Any]  # resolved: own preferences, else a team's defaults, else DEFAULT_PREFERENCES


class Directory(Protocol):
    def existing_users(self, conn: psycopg.Connection, user_ids: Iterable[str]) -> list[str]:
        """The subset of user_ids that exist (unknown users in a rule must not abort the batch)."""
        ...

    def team_members(self, conn: psycopg.Connection, team: str, role: str | None = None) -> list[str]: ...

    def on_call(self, conn: psycopg.Connection, team: str, now: datetime) -> list[str]:
        """The person on call now; the whole team if the team has no rotation."""
        ...

    def profiles(self, conn: psycopg.Connection, user_ids: Iterable[str], owner_team: str | None) -> dict[str, Profile]:
        """Time zone and preferences per user. Team defaults: the rule owner's team if the person is in it,
        else the person's first team (by id) that has defaults."""
        ...


def _resolve_preferences(
    user_id: str,
    own: Mapping[str, Mapping[str, Any]],
    team_defaults: Mapping[str, Mapping[str, Any]],
    teams_of_user: Mapping[str, list[str]],
    owner_team: str | None,
) -> Mapping[str, Any]:
    if user_id in own:
        return own[user_id]
    teams = teams_of_user.get(user_id, [])
    ordered = ([owner_team] if owner_team in teams else []) + sorted(t for t in teams if t != owner_team)
    for team in ordered:
        if team in team_defaults:
            return team_defaults[team]
    return DEFAULT_PREFERENCES


class SqlDirectory:
    """Reads users / teams / rotations / preferences in the caller's transaction."""

    def existing_users(self, conn: psycopg.Connection, user_ids: Iterable[str]) -> list[str]:
        ids = sorted(set(user_ids))
        if not ids:
            return []
        rows = conn.execute("SELECT id FROM users WHERE id = ANY(%s) ORDER BY id", (ids,)).fetchall()
        return [r[0] for r in rows]

    def team_members(self, conn: psycopg.Connection, team: str, role: str | None = None) -> list[str]:
        rows = conn.execute(
            """
            SELECT user_id FROM team_members
            WHERE team_id = %s AND (%s::text IS NULL OR role = %s)
            ORDER BY user_id
            """,
            (team, role, role),
        ).fetchall()
        return [r[0] for r in rows]

    def rotation(self, conn: psycopg.Connection, team: str) -> Rotation | None:
        row = conn.execute(
            """
            SELECT team_id, rotation, shift_length, handoff_time, timezone, starts_on
            FROM oncall_schedules WHERE team_id = %s
            """,
            (team,),
        ).fetchone()
        return Rotation(row[0], tuple(row[1]), row[2], row[3], row[4], row[5]) if row else None

    def on_call(self, conn: psycopg.Connection, team: str, now: datetime) -> list[str]:
        rotation = self.rotation(conn, team)
        if rotation is None:
            return self.team_members(conn, team)
        person = rotation.on_call(now)
        return self.existing_users(conn, [person]) if person else []

    def profiles(self, conn: psycopg.Connection, user_ids: Iterable[str], owner_team: str | None) -> dict[str, Profile]:
        ids = sorted(set(user_ids))
        if not ids:
            return {}
        timezones = dict(conn.execute("SELECT id, timezone FROM users WHERE id = ANY(%s)", (ids,)).fetchall())
        own = dict(
            conn.execute(
                "SELECT owner_id, by_severity FROM channel_preferences"
                " WHERE owner_type = 'user' AND owner_id = ANY(%s)",
                (ids,),
            ).fetchall()
        )
        teams_of_user: dict[str, list[str]] = {}
        team_defaults: dict[str, Mapping[str, Any]] = {}
        for user_id, team_id, by_severity in conn.execute(
            """
            SELECT m.user_id, m.team_id, p.by_severity
            FROM team_members m
            LEFT JOIN channel_preferences p ON p.owner_type = 'team' AND p.owner_id = m.team_id
            WHERE m.user_id = ANY(%s)
            """,
            (ids,),
        ).fetchall():
            teams_of_user.setdefault(user_id, []).append(team_id)
            if by_severity is not None:
                team_defaults[team_id] = by_severity
        return {
            user_id: Profile(
                timezone=timezones[user_id],
                by_severity=_resolve_preferences(user_id, own, team_defaults, teams_of_user, owner_team),
            )
            for user_id in ids
            if user_id in timezones
        }


@dataclass(frozen=True)
class StaticDirectory:
    """In-memory directory for tests: {team: {user_id: role}} plus optional rotations, zones, preferences."""

    teams: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    extra_users: frozenset[str] = frozenset()
    rotations: Mapping[str, Rotation] = field(default_factory=dict)
    timezones: Mapping[str, str] = field(default_factory=dict)
    user_preferences: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    team_preferences: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)

    def _users(self) -> set[str]:
        return set(self.extra_users).union(*(members.keys() for members in self.teams.values()))

    def existing_users(self, conn: psycopg.Connection, user_ids: Iterable[str]) -> list[str]:
        return sorted(set(user_ids) & self._users())

    def team_members(self, conn: psycopg.Connection, team: str, role: str | None = None) -> list[str]:
        members = self.teams.get(team, {})
        return sorted(user for user, user_role in members.items() if role is None or user_role == role)

    def on_call(self, conn: psycopg.Connection, team: str, now: datetime) -> list[str]:
        rotation = self.rotations.get(team)
        if rotation is None:
            return self.team_members(conn, team)
        person = rotation.on_call(now)
        return self.existing_users(conn, [person]) if person else []

    def profiles(self, conn: psycopg.Connection, user_ids: Iterable[str], owner_team: str | None) -> dict[str, Profile]:
        teams_of_user: dict[str, list[str]] = {}
        for team, members in self.teams.items():
            for user in members:
                teams_of_user.setdefault(user, []).append(team)
        return {
            user: Profile(
                timezone=self.timezones.get(user, "UTC"),
                by_severity=_resolve_preferences(
                    user, self.user_preferences, self.team_preferences, teams_of_user, owner_team
                ),
            )
            for user in sorted(set(user_ids) & self._users())
        }
