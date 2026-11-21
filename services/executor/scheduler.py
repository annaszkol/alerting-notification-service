"""Periodic jobs (spec §5 Scheduler). Each job is one idempotent SQL statement, so any number of executor
instances can run them at the same time without coordination.

Both jobs touch tables owned by A (`alerts`, `processed_events`); a job whose table does not exist yet is skipped.
"""

from __future__ import annotations

import logging
from datetime import datetime

import psycopg

log = logging.getLogger("scheduler")

INTERVAL_SECONDS = 10

SILENCE_SQL = """
UPDATE alerts SET state = 'resolved', resolved_how = 'silence', resolved_at = %(now)s
WHERE state <> 'resolved' AND auto_resolve_after IS NOT NULL AND last_seen < %(now)s - auto_resolve_after
RETURNING id
"""

CLEANUP_SQL = "DELETE FROM processed_events WHERE processed_at < %(now)s - interval '24 hours'"


def _exists(conn: psycopg.Connection, table: str) -> bool:
    return conn.execute("SELECT to_regclass(%s) IS NOT NULL", (table,)).fetchone()[0]


def resolve_silent_alerts(conn: psycopg.Connection, now: datetime) -> list[int]:
    """Alerts with autoResolveAfter and no new event for that long -> resolved (spec §5, §13 test 16)."""
    if not _exists(conn, "alerts"):
        return []
    with conn.transaction():
        ids = [row[0] for row in conn.execute(SILENCE_SQL, {"now": now}).fetchall()]
        for alert_id in ids:  # payload is only the id (8000-byte limit, spec §2)
            conn.execute("SELECT pg_notify('alerts_changed', %s)", (str(alert_id),))
    return ids


def clean_processed_events(conn: psycopg.Connection, now: datetime) -> int:
    """Idempotency rows older than 24 h (spec §5). A replay older than that is counted again (README)."""
    if not _exists(conn, "processed_events"):
        return 0
    with conn.transaction():
        return conn.execute(CLEANUP_SQL, {"now": now}).rowcount


def run_jobs(conn: psycopg.Connection, now: datetime) -> dict[str, int]:
    """Run every job; a failing job is logged and does not stop the others."""
    done: dict[str, int] = {}
    for name, job in (("silence", resolve_silent_alerts), ("processed_events", clean_processed_events)):
        try:
            result = job(conn, now)
            done[name] = len(result) if isinstance(result, list) else result
        except psycopg.Error as exc:
            log.error("scheduler job %s failed: %s", name, exc)
            done[name] = -1
    return done
