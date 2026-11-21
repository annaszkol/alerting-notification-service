"""Append-only audit log (migration 202611211701_audit_log.sql)."""

from __future__ import annotations

from typing import Any

import psycopg
from psycopg.types.json import Jsonb


def record(
    conn: psycopg.Connection,
    actor: str,
    action: str,
    target_type: str,
    target_id: str | int,
    details: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        "INSERT INTO audit_log (actor, action, target_type, target_id, details) VALUES (%s, %s, %s, %s, %s)",
        (actor, action, target_type, str(target_id), Jsonb(details or {})),
    )
