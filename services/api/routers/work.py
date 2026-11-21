"""Work time start / stop (spec §6 "Czas pracy"). Everyone manages only their own time; who is at work now is
visible to every signed-in user (the on-call screen shows it next to the person on duty)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends
from pydantic import BaseModel, StrictBool

from services.api.auth import CurrentUser
from services.api.db import get_conn
from services.api.errors import conflict
from services.delivery.presence import MAX_SESSION
from services.delivery.schedule import zone

router = APIRouter(tags=["work"])
Conn = Annotated[psycopg.Connection, Depends(get_conn)]


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _close_stale(conn: psycopg.Connection, user_id: str, now: datetime) -> None:
    conn.execute(
        """UPDATE work_sessions SET ended_at = started_at + %s, ended_how = 'auto'
           WHERE user_id = %s AND ended_at IS NULL AND started_at <= %s""",
        (MAX_SESSION, user_id, now - MAX_SESSION),
    )


def status(conn: psycopg.Connection, user_id: str, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    tracking = conn.execute("SELECT enabled FROM work_tracking WHERE user_id = %s", (user_id,)).fetchone()
    sessions = conn.execute(
        "SELECT started_at, ended_at, ended_how FROM work_sessions WHERE user_id = %s"
        " ORDER BY started_at DESC LIMIT 10",
        (user_id,),
    ).fetchall()
    open_ = next((s for s in sessions if s["ended_at"] is None and s["started_at"] > now - MAX_SESSION), None)
    tz = conn.execute("SELECT timezone FROM users WHERE id = %s", (user_id,)).fetchone()
    day_start = now.astimezone(zone(tz["timezone"] if tz else None)).replace(hour=0, minute=0, second=0, microsecond=0)
    today = timedelta()
    for s in sessions:
        end = s["ended_at"] or min(now, s["started_at"] + MAX_SESSION)
        start = max(s["started_at"], day_start)
        if end > start:
            today += end - start
    return {
        "tracking": bool(tracking and tracking["enabled"]),
        "active": open_ is not None,
        "startedAt": _iso(open_["started_at"]) if open_ else None,
        "autoStopAt": _iso(open_["started_at"] + MAX_SESSION) if open_ else None,
        "workedTodaySeconds": int(today.total_seconds()),
        "recent": [
            {"startedAt": _iso(s["started_at"]), "endedAt": _iso(s["ended_at"]), "endedHow": s["ended_how"]}
            for s in sessions
        ],
    }


@router.get("/me/work")
def get_my_work(conn: Conn, user: CurrentUser) -> dict[str, Any]:
    return status(conn, user.id)


@router.post("/me/work/start")
def start_work(conn: Conn, user: CurrentUser) -> dict[str, Any]:
    """Opens a session and switches tracking on (from now on: SMS / email / phone only while at work)."""
    now = datetime.now(UTC)
    conn.execute("SELECT pg_advisory_xact_lock(hashtext('work:' || %s))", (user.id,))
    _close_stale(conn, user.id, now)
    conn.execute(
        """INSERT INTO work_tracking (user_id, enabled) VALUES (%s, true)
           ON CONFLICT (user_id) DO UPDATE SET enabled = true, updated_at = now()""",
        (user.id,),
    )
    conn.execute(
        "INSERT INTO work_sessions (user_id, started_at) VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (user.id, now),
    )  # already at work: the open session stays (start twice = still at work)
    return status(conn, user.id, now)


@router.post("/me/work/stop")
def stop_work(conn: Conn, user: CurrentUser) -> dict[str, Any]:
    now = datetime.now(UTC)
    conn.execute("SELECT pg_advisory_xact_lock(hashtext('work:' || %s))", (user.id,))
    _close_stale(conn, user.id, now)
    closed = conn.execute(
        "UPDATE work_sessions SET ended_at = %s, ended_how = 'user'"
        " WHERE user_id = %s AND ended_at IS NULL RETURNING id",
        (now, user.id),
    ).fetchall()
    if not closed:
        raise conflict("you are not at work (no open session)")
    return status(conn, user.id, now)


class TrackingRequest(BaseModel):
    model_config = {"extra": "forbid"}
    tracking: StrictBool


@router.put("/me/work")
def set_tracking(body: TrackingRequest, conn: Conn, user: CurrentUser) -> dict[str, Any]:
    """Switch "notify me only while at work" on or off. Off: notifications as before (preferences, rules)."""
    conn.execute(
        """INSERT INTO work_tracking (user_id, enabled) VALUES (%s, %s)
           ON CONFLICT (user_id) DO UPDATE SET enabled = EXCLUDED.enabled, updated_at = now()""",
        (user.id, body.tracking),
    )
    return status(conn, user.id)


@router.get("/work/active")
def list_at_work(conn: Conn, _: CurrentUser) -> list[dict[str, Any]]:
    """People who track their time and are at work now."""
    now = datetime.now(UTC)
    rows = conn.execute(
        """SELECT s.user_id, s.started_at FROM work_sessions s JOIN work_tracking t ON t.user_id = s.user_id
           WHERE t.enabled AND s.ended_at IS NULL AND s.started_at > %s ORDER BY s.user_id""",
        (now - MAX_SESSION,),
    ).fetchall()
    return [{"userId": r["user_id"], "startedAt": _iso(r["started_at"])} for r in rows]
