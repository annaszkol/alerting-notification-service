"""What the executor needs from alerts (table owned by A, columns as in spec §8)."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Protocol

import psycopg

from contracts.actions import AlertContext


class AlertGateway(Protocol):
    def get(self, conn: psycopg.Connection, alert_id: int) -> AlertContext | None: ...

    def resolve(self, conn: psycopg.Connection, alert_id: int, how: str, by: str | None, now: datetime) -> bool:
        """Resolve if still open; NOTIFY alerts_changed (spec §2). Returns whether it changed."""
        ...


class SqlAlerts:
    def get(self, conn: psycopg.Connection, alert_id: int) -> AlertContext | None:
        row = conn.execute(
            "SELECT id, state, owner, severity, first_event FROM alerts WHERE id = %s", (alert_id,)
        ).fetchone()
        if row is None:
            return None
        return AlertContext(id=row[0], state=row[1], owner=row[2] or {}, severity=row[3], letter=row[4] or {})

    def resolve(self, conn: psycopg.Connection, alert_id: int, how: str, by: str | None, now: datetime) -> bool:
        cur = conn.execute(
            """UPDATE alerts SET state = 'resolved', resolved_how = %s, resolved_by = %s, resolved_at = %s
               WHERE id = %s AND state <> 'resolved'""",
            (how, by, now, alert_id),
        )
        if cur.rowcount:
            notify_alerts_changed(conn, [alert_id])
        return cur.rowcount == 1


NOTIFY_CHUNK = 500  # ids per message: keeps the payload far below Postgres' 8000-byte limit


def notify_alerts_changed(conn: psycopg.Connection, alert_ids: list[int]) -> None:
    """`NOTIFY alerts_changed` with payload {"ids": [...]} (format proposed by C in docs/plans/c-api-web.md).
    Delivered only after COMMIT, so the UI never sees a rolled-back change."""
    for start in range(0, len(alert_ids), NOTIFY_CHUNK):
        payload = json.dumps({"ids": alert_ids[start : start + NOTIFY_CHUNK]}, separators=(",", ":"))
        conn.execute("SELECT pg_notify('alerts_changed', %s)", (payload,))
