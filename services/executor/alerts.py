"""What the executor needs from alerts (table owned by A, columns as in spec §8)."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

import psycopg

from contracts.actions import AlertContext


class AlertGateway(Protocol):
    def get(self, conn: psycopg.Connection, alert_id: int) -> AlertContext | None: ...

    def resolve(self, conn: psycopg.Connection, alert_id: int, how: str, by: str | None, now: datetime) -> bool:
        """Resolve if still open; NOTIFY alerts_changed with the id (spec §2). Returns whether it changed."""
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
            conn.execute("SELECT pg_notify('alerts_changed', %s)", (str(alert_id),))
        return cur.rowcount == 1
