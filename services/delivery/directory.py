"""Who is who: users, team membership, on-call rotations, time zones and channel preferences.

Used by plan_notifications inside the processor's transaction, so every lookup is a plain read.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

import psycopg

from services.delivery.presence import away_users
from services.delivery.schedule import DEFAULT_PREFERENCES, Limit, Rotation, merge_limits


@dataclass(frozen=True)
class Profile:
    """What routing needs to know about one person."""

    timezone: str
    # resolved: own preferences, else a team's defaults, else DEFAULT_PREFERENCES
    by_severity: Mapping[str, Any]
    # throttling per channel: DEFAULT_LIMITS < that team's limits < own limits
    limits: Mapping[str, Limit] = field(default_factory=dict)


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

    def away(self, conn: psycopg.Connection, user_ids: Iterable[str], now: datetime) -> set[str]:
        """People who track their work time and are not at work now (spec §6 "Czas pracy")."""
        ...


def _team_order(user_id: str, teams_of_user: Mapping[str, list[str]], owner_team: str | None) -> list[str]:
    """Rule owner's team first (if the person is in it), then the person's other teams by id."""
    teams = teams_of_user.get(user_id, [])
    return ([owner_team] if owner_team in teams else []) + sorted(t for t in teams if t != owner_team)


def _resolve(
    user_id: str,
    own: Mapping[str, Mapping[str, Any]],
    team_prefs: Mapping[str, Mapping[str, Any]],
    team_limits: Mapping[str, Mapping[str, Any]],
    own_limits: Mapping[str, Mapping[str, Any]],
    teams_of_user: Mapping[str, list[str]],
    owner_team: str | None,
    timezone: str,
) -> Profile:
    order = _team_order(user_id, teams_of_user, owner_team)
    by_severity = own.get(user_id) or next((team_prefs[t] for t in order if t in team_prefs), DEFAULT_PREFERENCES)
    team_layer = next((team_limits[t] for t in order if t in team_limits), None)
    return Profile(timezone, by_severity, merge_limits(team_layer, own_limits.get(user_id)))


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
        own_prefs: dict[str, Mapping[str, Any]] = {}
        own_limits: dict[str, Mapping[str, Any]] = {}
        for user_id, by_severity, limits in conn.execute(
            "SELECT owner_id, by_severity, limits FROM channel_preferences"
            " WHERE owner_type = 'user' AND owner_id = ANY(%s)",
            (ids,),
        ).fetchall():
            if by_severity:
                own_prefs[user_id] = by_severity
            if limits:
                own_limits[user_id] = limits
        teams_of_user: dict[str, list[str]] = {}
        team_prefs: dict[str, Mapping[str, Any]] = {}
        team_limits: dict[str, Mapping[str, Any]] = {}
        for user_id, team_id, by_severity, limits in conn.execute(
            """
            SELECT m.user_id, m.team_id, p.by_severity, p.limits
            FROM team_members m
            LEFT JOIN channel_preferences p ON p.owner_type = 'team' AND p.owner_id = m.team_id
            WHERE m.user_id = ANY(%s)
            """,
            (ids,),
        ).fetchall():
            teams_of_user.setdefault(user_id, []).append(team_id)
            if by_severity:
                team_prefs[team_id] = by_severity
            if limits:
                team_limits[team_id] = limits
        return {
            user_id: _resolve(
                user_id, own_prefs, team_prefs, team_limits, own_limits, teams_of_user, owner_team, timezones[user_id]
            )
            for user_id in ids
            if user_id in timezones
        }

    def away(self, conn: psycopg.Connection, user_ids: Iterable[str], now: datetime) -> set[str]:
        return away_users(conn, user_ids, now)


@dataclass(frozen=True)
class StaticDirectory:
    """In-memory directory for tests: {team: {user_id: role}} plus optional rotations, zones, preferences."""

    teams: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    extra_users: frozenset[str] = frozenset()
    rotations: Mapping[str, Rotation] = field(default_factory=dict)
    timezones: Mapping[str, str] = field(default_factory=dict)
    user_preferences: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    team_preferences: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    user_limits: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    team_limits: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    away_users: frozenset[str] = frozenset()

    def away(self, conn: psycopg.Connection, user_ids: Iterable[str], now: datetime) -> set[str]:
        return set(user_ids) & self.away_users

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
            user: _resolve(
                user,
                {u: p for u, p in self.user_preferences.items() if p},
                {t: p for t, p in self.team_preferences.items() if p},
                {t: x for t, x in self.team_limits.items() if x},
                {u: x for u, x in self.user_limits.items() if x},
                teams_of_user,
                owner_team,
                self.timezones.get(user, "UTC"),
            )
            for user in sorted(set(user_ids) & self._users())
        }
