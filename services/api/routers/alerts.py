"""Alert feed, details, timeline, ack / resolve (spec §5, §9 P1). Every logged-in user can read alerts and
acknowledge or resolve them (spec §9: permissions only where they matter)."""

from __future__ import annotations

from typing import Annotated, Any, Literal

import psycopg
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from services.api.auth import CurrentUser
from services.api.db import get_conn
from services.api.domain import alerts

router = APIRouter(tags=["alerts"])
Conn = Annotated[psycopg.Connection, Depends(get_conn)]
Severity = Literal["info", "warning", "error", "critical"]
State = Literal["open", "acknowledged", "resolved"]
Slug = Annotated[str, Query(pattern=r"^[a-z0-9][a-z0-9_.-]*$", max_length=100)]


def _base(request: Request) -> str:
    return request.app.state.settings.public_base_url


@router.get("/alerts")
def list_alerts(
    request: Request,
    conn: Conn,
    _: CurrentUser,
    team: Slug | None = None,
    user: Slug | None = None,
    severity: Annotated[list[Severity] | None, Query()] = None,
    state: Annotated[list[State] | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    items, next_cursor = alerts.list_alerts(
        conn,
        _base(request),
        team=team,
        user=user,
        severities=severity,
        states=state,
        q=q,
        cursor=cursor,
        limit=limit,
    )
    return {"items": items, "nextCursor": next_cursor}


@router.get("/alerts/{alertId}")
def get_alert(alertId: int, request: Request, conn: Conn, _: CurrentUser) -> dict[str, Any]:
    return alerts.get_detail(conn, _base(request), alertId)


@router.get("/alerts/{alertId}/timeline")
def get_timeline(alertId: int, conn: Conn, _: CurrentUser) -> list[dict[str, Any]]:
    return alerts.timeline(conn, alertId)


@router.post("/alerts/{alertId}/ack")
def ack(alertId: int, request: Request, conn: Conn, user: CurrentUser) -> dict[str, Any]:
    alerts.ack_alert(conn, alertId, user.id, via="ui")
    return alerts.get_summary(conn, _base(request), alertId)


class ResolveRequest(BaseModel):
    model_config = {"extra": "forbid"}
    note: str | None = Field(default=None, max_length=2000)


@router.post("/alerts/{alertId}/resolve")
def resolve(
    alertId: int, request: Request, conn: Conn, user: CurrentUser, body: ResolveRequest | None = None
) -> dict[str, Any]:
    alerts.resolve_alert(conn, alertId, user.id, note=body.note if body else None, via="ui")
    return alerts.get_summary(conn, _base(request), alertId)
