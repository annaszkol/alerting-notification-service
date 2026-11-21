"""Work time start / stop (spec §6 "Czas pracy"): who is away now, and what an away person still gets.

A person is *away* when they switched on work tracking (`work_tracking.enabled`) and have no session that is open
and younger than MAX_SESSION. The age limit makes a forgotten "Stop" harmless even before the scheduler closes it.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta

import psycopg

from contracts.delivery import Channel, Severity

MAX_SESSION = timedelta(hours=12)

AWAY_SQL = """
SELECT t.user_id FROM work_tracking t
WHERE t.enabled AND t.user_id = ANY(%(ids)s)
  AND NOT EXISTS (
      SELECT 1 FROM work_sessions s
      WHERE s.user_id = t.user_id AND s.ended_at IS NULL AND s.started_at > %(since)s AND s.started_at <= %(now)s
  )
"""

CLOSE_STALE_SQL = """
UPDATE work_sessions SET ended_at = started_at + %(max)s, ended_how = 'auto'
WHERE ended_at IS NULL AND started_at <= %(since)s
RETURNING id
"""


def away_users(conn: psycopg.Connection, user_ids: Iterable[str], now: datetime) -> set[str]:
    ids = sorted(set(user_ids))
    if not ids or conn.execute("SELECT to_regclass('work_tracking') IS NULL").fetchone()[0]:
        return set()
    rows = conn.execute(AWAY_SQL, {"ids": ids, "since": now - MAX_SESSION, "now": now}).fetchall()
    return {r[0] for r in rows}


def close_stale_sessions(conn: psycopg.Connection, now: datetime) -> int:
    """Scheduler job: sessions open longer than MAX_SESSION end at start + MAX_SESSION (`ended_how = 'auto'`)."""
    if conn.execute("SELECT to_regclass('work_sessions') IS NULL").fetchone()[0]:
        return 0
    return len(conn.execute(CLOSE_STALE_SQL, {"max": MAX_SESSION, "since": now - MAX_SESSION}).fetchall())


def allowed_while_away(channel: Channel, severity: Severity) -> bool:
    """The UI feed always; a critical phone call always (like throttling: nobody misses an outage)."""
    return channel is Channel.UI or (channel is Channel.PHONE and severity is Severity.CRITICAL)
