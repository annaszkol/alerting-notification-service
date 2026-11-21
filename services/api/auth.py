"""Mock login + JWT, and role checks (spec §1 Role, §9).

The token carries only `sub` (user id). Roles are read from the database on every request, so a role change
works immediately. Permission checks live here so every router asks the same questions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import jwt
import psycopg
from fastapi import Depends, Request

from services.api.db import get_conn
from services.api.errors import forbidden, unauthorized
from services.api.settings import Settings

ALGORITHM = "HS256"


@dataclass(frozen=True)
class Principal:
    id: str
    name: str
    is_admin: bool
    teams: dict[str, str] = field(default_factory=dict)  # team id -> role ('manager' | 'member')

    def is_member(self, team: str) -> bool:
        return self.is_admin or team in self.teams

    def is_manager(self, team: str) -> bool:
        return self.is_admin or self.teams.get(team) == "manager"

    def can_edit_owned(self, owner: dict[str, Any]) -> bool:
        """Rules and other owned config: manager of the owner team, the owner user, or admin."""
        if self.is_admin:
            return True
        if "team" in owner:
            return self.teams.get(owner["team"]) == "manager"
        return owner.get("user") == self.id


def create_token(settings: Settings, user_id: str, now: datetime | None = None) -> tuple[str, datetime]:
    issued = now or datetime.now(UTC)
    expires = issued + timedelta(seconds=settings.jwt_ttl_seconds)
    token = jwt.encode(
        {"sub": user_id, "iat": issued, "exp": expires}, settings.jwt_secret, algorithm=ALGORITHM
    )
    return token, expires


def decode_token(settings: Settings, token: str) -> str:
    """User id from a valid token; raises `unauthorized` otherwise."""
    try:
        claims = jwt.decode(
            token, settings.jwt_secret, algorithms=[ALGORITHM], options={"require": ["sub", "exp"]}
        )
    except jwt.ExpiredSignatureError as exc:
        raise unauthorized("token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise unauthorized("invalid token") from exc
    return str(claims["sub"])


def load_principal(conn: psycopg.Connection, user_id: str) -> Principal | None:
    row = conn.execute("SELECT id, name, is_admin FROM users WHERE id = %s", (user_id,)).fetchone()
    if row is None:
        return None
    teams = conn.execute("SELECT team_id, role FROM team_members WHERE user_id = %s", (user_id,)).fetchall()
    return Principal(
        id=row["id"],
        name=row["name"],
        is_admin=row["is_admin"],
        teams={t["team_id"]: t["role"] for t in teams},
    )


def bearer_token(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise unauthorized()
    return token.strip()


def current_user(request: Request, conn: Annotated[psycopg.Connection, Depends(get_conn)]) -> Principal:
    user_id = decode_token(request.app.state.settings, bearer_token(request))
    principal = load_principal(conn, user_id)
    if principal is None:
        raise unauthorized("user no longer exists")
    return principal


CurrentUser = Annotated[Principal, Depends(current_user)]


def require_admin(user: CurrentUser) -> Principal:
    if not user.is_admin:
        raise forbidden("admin role required")
    return user


AdminUser = Annotated[Principal, Depends(require_admin)]
