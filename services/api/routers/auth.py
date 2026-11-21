"""Mock login (spec §9): pick a seeded user, get a JWT. Documented as a mock in the README."""

from __future__ import annotations

from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from services.api.auth import CurrentUser, create_token
from services.api.db import get_conn
from services.api.errors import not_found

router = APIRouter(tags=["auth"])
Conn = Annotated[psycopg.Connection, Depends(get_conn)]

_USER_COLUMNS = "id, name, email, phone, timezone, is_admin"


def user_to_api(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "email": row["email"],
        "phone": row["phone"],
        "timezone": row["timezone"],
        "isAdmin": row["is_admin"],
    }


class LoginRequest(BaseModel):
    model_config = {"extra": "forbid"}
    userId: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]*$", max_length=100)


@router.get("/auth/users")
def list_login_users(conn: Conn) -> list[dict[str, Any]]:
    return [user_to_api(r) for r in conn.execute(f"SELECT {_USER_COLUMNS} FROM users ORDER BY name, id")]


@router.post("/auth/login")
def login(body: LoginRequest, request: Request, conn: Conn) -> dict[str, Any]:
    row = conn.execute(f"SELECT {_USER_COLUMNS} FROM users WHERE id = %s", (body.userId,)).fetchone()
    if row is None:
        raise not_found(f"user {body.userId}")
    token, expires = create_token(request.app.state.settings, row["id"])
    return {"token": token, "expiresAt": expires.isoformat(), "user": user_to_api(row)}


@router.get("/me")
def me(user: CurrentUser, conn: Conn) -> dict[str, Any]:
    row = conn.execute(f"SELECT {_USER_COLUMNS} FROM users WHERE id = %s", (user.id,)).fetchone()
    teams = conn.execute(
        "SELECT t.id, t.name, m.role FROM team_members m JOIN teams t ON t.id = m.team_id"
        " WHERE m.user_id = %s ORDER BY t.name",
        (user.id,),
    ).fetchall()
    return {
        "user": user_to_api(row),
        "isAdmin": user.is_admin,
        "teams": [{"id": t["id"], "name": t["name"], "role": t["role"]} for t in teams],
    }
