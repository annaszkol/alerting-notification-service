"""Alert history written by B: runbook runs, steps, approvals and automatic resolves (spec §7 "kto i co zrobił", §8).

Every entry goes to C's append-only `audit_log` (migration 202611211701) as `target_type = 'alert'` with the alert id,
so the alert timeline (`GET /api/alerts/{id}/timeline`) shows it without extra queries; run, step and approval ids are
in `details`. Each entry also sends `NOTIFY alerts_changed` for the alert (contracts/notify.md), in the same
transaction as the change it describes.

Actors are user ids, or `executor` / `scheduler` for automatic actions.
"""

from __future__ import annotations

from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from contracts.notify import notify_alerts_changed


def record(conn: psycopg.Connection, alert_id: int, actor: str | None, action: str, **details: Any) -> None:
    """`action`: run.started | run.cancelled | step.started | step.succeeded | step.attempt_failed | step.failed |
    step.skipped | step.done | approval.requested | approval.approved | approval.rejected | alert.resolved."""
    conn.execute(
        "INSERT INTO audit_log (actor, action, target_type, target_id, details) VALUES (%s, %s, 'alert', %s, %s)",
        (actor or "executor", action, str(alert_id), Jsonb({k: v for k, v in details.items() if v is not None})),
    )
    notify_alerts_changed(conn, [alert_id])
