"""Runtime config changes (spec §4): bump `config_version` and `NOTIFY config_changed` in one transaction."""

from __future__ import annotations

import psycopg

from contracts.notify import CONFIG_CHANGED, config_changed_payload


def bump_config_version(conn: psycopg.Connection) -> int:
    row = conn.execute("UPDATE config_version SET version = version + 1 RETURNING version").fetchone()
    if row is None:
        raise RuntimeError("config_version has no row")
    version = int(row["version"] if isinstance(row, dict) else row[0])
    conn.execute("SELECT pg_notify(%s, %s)", (CONFIG_CHANGED, config_changed_payload(version)))
    return version
