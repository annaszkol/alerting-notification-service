"""Who is who: users, team membership and (later) on-call rotations. Used by plan_notifications."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

import psycopg


class Directory(Protocol):
    def existing_users(self, conn: psycopg.Connection, user_ids: Iterable[str]) -> list[str]:
        """The subset of user_ids that exist (unknown users in a rule must not abort the batch)."""
        ...

    def team_members(self, conn: psycopg.Connection, team: str, role: str | None = None) -> list[str]: ...

    def on_call(self, conn: psycopg.Connection, team: str, now: datetime) -> list[str]: ...


class SqlDirectory:
    """Reads users / team_members in the caller's transaction."""

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

    def on_call(self, conn: psycopg.Connection, team: str, now: datetime) -> list[str]:
        # Stub: no rotations yet (feat/preferences-oncall), so the whole team is "on call".
        return self.team_members(conn, team)


@dataclass(frozen=True)
class StaticDirectory:
    """In-memory directory: {team: {user_id: role}} plus users outside any team. For tests."""

    teams: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    extra_users: frozenset[str] = frozenset()

    def existing_users(self, conn: psycopg.Connection, user_ids: Iterable[str]) -> list[str]:
        known = set(self.extra_users).union(*(members.keys() for members in self.teams.values()))
        return sorted(set(user_ids) & known)

    def team_members(self, conn: psycopg.Connection, team: str, role: str | None = None) -> list[str]:
        members = self.teams.get(team, {})
        return sorted(user for user, user_role in members.items() if role is None or user_role == role)

    def on_call(self, conn: psycopg.Connection, team: str, now: datetime) -> list[str]:
        return self.team_members(conn, team)
