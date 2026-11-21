"""Alert reads and the manual lifecycle transitions (spec §5 "Cykl życia").

`ack_alert` and `resolve_alert` are the single implementation of these transitions for the UI, inbound
SMS (`ACK <code>`) and the phone "press 1" (plan: agreement 3). They run in the caller's transaction, write
the audit log and send `NOTIFY alerts_changed`, so the change, its audit entry and the UI push commit or
roll back together. The state check is part of the UPDATE (`WHERE state = ...`), so a race with the
processor or another user is safe.
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row

from contracts.notify import notify_alerts_changed
from services.api.domain import audit
from services.api.durations import format_duration
from services.api.errors import ApiError
from services.api.stubs import MISSING, get_path, render_live

DEFAULT_STATES = ("open", "acknowledged")


class AlertNotFound(ApiError):
    def __init__(self, alert_id: int) -> None:
        super().__init__("not_found", f"alert {alert_id} not found")


class AlertStateConflict(ApiError):
    def __init__(self, alert_id: int, state: str, wanted: str) -> None:
        super().__init__("conflict", f"alert {alert_id} is {state}; cannot {wanted}", {"state": state})


# `version` = when the row was read (clock_timestamp, one database clock). A newer read is never staler
# than an older one, so clients drop out-of-order WebSocket / REST results by comparing versions as strings.
_SELECT = """
SELECT a.*,
       EXISTS (SELECT 1 FROM notifications n WHERE n.alert_id = a.id AND n.status = 'dead') AS has_dead,
       to_char(clock_timestamp() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS read_at
FROM alerts a
"""


# --- shapes --------------------------------------------------------------------------------------------


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def alert_url(base_url: str, alert_id: int) -> str:
    return f"{base_url}/a/{alert_id}"


def _live(row: dict[str, Any], base_url: str) -> dict[str, Any]:
    return {"$count": row["count"], "$state": row["state"], "$alertUrl": alert_url(base_url, row["id"])}


def _teams(row: dict[str, Any]) -> list[str]:
    teams = [row["owner"]["team"]] if "team" in row["owner"] else []
    for team in row.get("extra_teams") or []:
        if team not in teams:
            teams.append(team)
    return teams


def summary(row: dict[str, Any], base_url: str) -> dict[str, Any]:
    """AlertSummary (contracts/openapi.yaml) with live fields rendered."""
    return {
        "id": row["id"],
        "ruleId": row["rule_id"],
        "owner": row["owner"],
        "teams": _teams(row),
        "state": row["state"],
        "severity": row["severity"],
        "title": render_live(row["title"], _live(row, base_url)),
        "color": row.get("color"),
        "count": row["count"],
        "firstSeen": _iso(row["first_seen"]),
        "lastSeen": _iso(row["last_seen"]),
        "hasDeadNotification": row["has_dead"],
        "version": row["read_at"],
    }


# --- reads ---------------------------------------------------------------------------------------------


def encode_cursor(row: dict[str, Any]) -> str:
    raw = json.dumps([row["last_seen"].isoformat(), row["id"]]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, int]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        last_seen, alert_id = json.loads(base64.urlsafe_b64decode(padded))
        return datetime.fromisoformat(last_seen), int(alert_id)
    except (ValueError, TypeError, binascii.Error) as exc:
        raise ApiError("invalid", "invalid cursor", [{"path": "cursor", "message": "invalid cursor"}]) from exc


def list_alerts(
    conn: psycopg.Connection,
    base_url: str,
    *,
    team: str | None = None,
    user: str | None = None,
    severities: Sequence[str] | None = None,
    states: Sequence[str] | None = None,
    q: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> tuple[list[dict[str, Any]], str | None]:
    where, params = [], []
    if team:
        where.append("(a.owner->>'team' = %s OR %s = ANY(a.extra_teams))")
        params += [team, team]
    if user:
        where.append("a.owner->>'user' = %s")
        params.append(user)
    if severities:
        where.append("a.severity = ANY(%s)")
        params.append(list(severities))
    where.append("a.state = ANY(%s)")
    params.append(list(states or DEFAULT_STATES))
    if q:
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        where.append("a.title ILIKE %s")
        params.append(f"%{escaped}%")
    if cursor:
        where.append("(a.last_seen, a.id) < (%s, %s)")
        params += list(decode_cursor(cursor))
    sql = f"{_SELECT} WHERE {' AND '.join(where)} ORDER BY a.last_seen DESC, a.id DESC LIMIT %s"
    rows = conn.cursor(row_factory=dict_row).execute(sql, [*params, limit + 1]).fetchall()
    next_cursor = encode_cursor(rows[limit - 1]) if len(rows) > limit else None
    return [summary(r, base_url) for r in rows[:limit]], next_cursor


def get_rows(conn: psycopg.Connection, alert_ids: Sequence[int]) -> list[dict[str, Any]]:
    """Raw rows (with `has_dead`, `read_at`) for the WebSocket fan-out and detail views."""
    if not alert_ids:
        return []
    return (
        conn.cursor(row_factory=dict_row)
        .execute(f"{_SELECT} WHERE a.id = ANY(%s) ORDER BY a.id", (list(alert_ids),))
        .fetchall()
    )


def _row(conn: psycopg.Connection, alert_id: int) -> dict[str, Any]:
    rows = get_rows(conn, [alert_id])
    if not rows:
        raise AlertNotFound(alert_id)
    return rows[0]


def get_summary(conn: psycopg.Connection, base_url: str, alert_id: int) -> dict[str, Any]:
    return summary(_row(conn, alert_id), base_url)


def get_detail(conn: psycopg.Connection, base_url: str, alert_id: int) -> dict[str, Any]:
    row = _row(conn, alert_id)
    cur = conn.cursor(row_factory=dict_row)
    notifications = cur.execute(
        "SELECT id, user_id, channel, kind, status, attempts, last_error, created_at, sent_at"
        " FROM notifications WHERE alert_id = %s ORDER BY created_at, id",
        (alert_id,),
    ).fetchall()
    note = cur.execute(
        "SELECT details->>'note' AS note FROM audit_log WHERE target_type = 'alert' AND target_id = %s"
        " AND action = 'alert.resolved' ORDER BY ts DESC, id DESC LIMIT 1",
        (str(alert_id),),
    ).fetchone()
    fields = []
    for path in row.get("fields") or []:
        value = get_path(row["first_event"], path)
        fields.append({"path": path, "value": None if value is MISSING else value})
    return {
        **summary(row, base_url),
        "body": render_live(row.get("body"), _live(row, base_url)),
        "fields": fields,
        "correlationKey": row.get("correlation_key"),
        "firstEvent": row["first_event"],
        "lastEvent": row["last_event"],
        "ackedBy": row.get("acked_by"),
        "ackedAt": _iso(row.get("acked_at")),
        "resolvedBy": row.get("resolved_by"),
        "resolvedAt": _iso(row.get("resolved_at")),
        "resolvedHow": row.get("resolved_how"),
        "resolveNote": note["note"] if note else None,
        "previousAlertId": row.get("previous_alert_id"),
        "autoResolveAfter": format_duration(row.get("auto_resolve_after")),
        "notifications": [
            {
                "id": n["id"],
                "userId": n["user_id"],
                "channel": n["channel"],
                "kind": n["kind"],
                "status": n["status"],
                "attempts": n["attempts"],
                "lastError": n["last_error"],
                "createdAt": _iso(n["created_at"]),
                "sentAt": _iso(n["sent_at"]),
            }
            for n in notifications
        ],
        "runbooks": [],  # filled in by feat/web-runbook-view
    }


def _audit_summary(action: str, actor: str, details: dict[str, Any]) -> str:
    via = f" via {details['via']}" if details.get("via") else ""
    match action:
        case "alert.created":
            return "alert created"
        case "alert.acknowledged":
            return f"acknowledged by {actor}{via}"
        case "alert.resolved":
            how = details.get("how", "manual")
            text = f"resolved by {actor}{via}" if how == "manual" else f"resolved ({how})"
            return f"{text}: {details['note']}" if details.get("note") else text
        case "run.started":
            return f"runbook {details.get('runbook', '')} started by {actor} ({details.get('mode', 'manual')})"
        case "run.cancelled":
            return f"runbook run cancelled by {actor}"
        case "step.started":
            return f"step {details.get('step')} started by {actor}"
        case "step.succeeded":
            return f"step {details.get('step')} succeeded"
        case "step.attempt_failed":
            return f"step {details.get('step')} attempt {details.get('attempt')} failed: {details.get('error')}"
        case "step.failed":
            return f"step {details.get('step')} failed: {details.get('error')}"
        case "step.skipped":
            return f"step {details.get('step')} skipped ({details.get('reason', '')})"
        case "step.done":
            return f"step {details.get('step')} done by {actor}"
        case "approval.requested":
            return f"approval of {details.get('step')} requested from {details.get('team')} {details.get('role')}s"
        case "approval.approved" | "approval.rejected":
            return f"{details.get('step')} {action.split('.')[1]} by {actor}{via}"
        case _:
            return f"{action} by {actor}{via}"


def _audit_kind(action: str) -> str:
    """TimelineEntry.kind (contracts/openapi.yaml): runbook entries written by B's executor are steps / approvals."""
    if action.startswith(("step.", "run.")):
        return "step"
    if action.startswith("approval."):
        return "approval"
    return "state"


def timeline(conn: psycopg.Connection, alert_id: int) -> list[dict[str, Any]]:
    _row(conn, alert_id)  # 404 for unknown alerts
    cur = conn.cursor(row_factory=dict_row)
    entries: list[dict[str, Any]] = []
    for a in cur.execute(
        "SELECT ts, actor, action, details FROM audit_log WHERE target_type = 'alert' AND target_id = %s",
        (str(alert_id),),
    ):
        entries.append(
            {
                "ts": a["ts"],
                "kind": _audit_kind(a["action"]),
                "actor": a["actor"],
                "summary": _audit_summary(a["action"], a["actor"], a["details"]),
                "details": {"action": a["action"], **a["details"]},
            }
        )
    for n in cur.execute(
        "SELECT id, user_id, channel, kind, status, attempts, last_error, created_at, sent_at"
        " FROM notifications WHERE alert_id = %s",
        (alert_id,),
    ):
        text = f"{n['kind']} to {n['user_id']} via {n['channel']}: {n['status']}"
        if n["last_error"]:
            text += f" ({n['last_error']})"
        entries.append(
            {
                "ts": n["sent_at"] or n["created_at"],
                "kind": "notification",
                "actor": "delivery",
                "summary": text,
                "details": {"notificationId": n["id"], "attempts": n["attempts"], "status": n["status"]},
            }
        )
    entries.sort(key=lambda e: e["ts"])
    return [{**e, "ts": _iso(e["ts"])} for e in entries]


# --- transitions ---------------------------------------------------------------------------------------


def _state(conn: psycopg.Connection, alert_id: int) -> str:
    row = conn.execute("SELECT state FROM alerts WHERE id = %s", (alert_id,)).fetchone()
    if row is None:
        raise AlertNotFound(alert_id)
    return row["state"] if isinstance(row, dict) else row[0]


def ack_alert(conn: psycopg.Connection, alert_id: int, actor: str, via: str = "ui") -> None:
    """open -> acknowledged. Raises AlertNotFound / AlertStateConflict. `via`: ui | sms | phone."""
    updated = conn.execute(
        "UPDATE alerts SET state = 'acknowledged', acked_by = %s, acked_at = now()"
        " WHERE id = %s AND state = 'open' RETURNING id",
        (actor, alert_id),
    ).fetchone()
    if updated is None:
        raise AlertStateConflict(alert_id, _state(conn, alert_id), "acknowledge")
    audit.record(conn, actor, "alert.acknowledged", "alert", alert_id, {"via": via})
    notify_alerts_changed(conn, [alert_id])


def resolve_alert(
    conn: psycopg.Connection,
    alert_id: int,
    actor: str,
    *,
    note: str | None = None,
    how: str = "manual",
    via: str | None = "ui",
) -> None:
    """open | acknowledged -> resolved. `how`: manual | runbook (event and silence belong to A and B)."""
    updated = conn.execute(
        "UPDATE alerts SET state = 'resolved', resolved_by = %s, resolved_at = now(), resolved_how = %s"
        " WHERE id = %s AND state <> 'resolved' RETURNING id",
        (actor, how, alert_id),
    ).fetchone()
    if updated is None:
        raise AlertStateConflict(alert_id, _state(conn, alert_id), "resolve")
    details: dict[str, Any] = {"how": how}
    if via:
        details["via"] = via
    if note:
        details["note"] = note
    audit.record(conn, actor, "alert.resolved", "alert", alert_id, details)
    notify_alerts_changed(conn, [alert_id])
