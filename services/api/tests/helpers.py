"""Data helpers for API tests."""

from __future__ import annotations

from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from services.api import schemas

LETTER = {
    "$eventId": "evt-1",
    "$sourceId": "http-default",
    "$sourceType": "http",
    "$schema": "workflow",
    "$severity": "critical",
    "$service": "order-settlement",
    "payload": {
        "type": "WORKFLOW_FAILED",
        "workflowId": "wf-123",
        "step": "confirm",
        "error": {"code": "TIMEOUT", "message": "counterparty timeout"},
        "labels": {"app.kubernetes.io/name": "settlement"},
    },
}


def insert_alert(conn: psycopg.Connection, **values: Any) -> int:
    row = {
        "rule_id": "settlements-prod-failures",
        "owner": {"team": "settlements"},
        "fingerprint": f"fp-{values.get('title', 'x')}-{values.get('state', 'open')}",
        "state": "open",
        "severity": "critical",
        "title": "Settlement failed: wf-123",
        "first_event": LETTER,
        "last_event": LETTER,
        **values,
    }
    for key in ("owner", "first_event", "last_event", "fields"):
        if key in row:
            row[key] = Jsonb(row[key])
    columns = ", ".join(row)
    placeholders = ", ".join(f"%({k})s" for k in row)
    return conn.execute(f"INSERT INTO alerts ({columns}) VALUES ({placeholders}) RETURNING id", row).fetchone()["id"]


def assert_matches(schema: str, data: Any) -> None:
    errors = schemas.errors(schema, data)
    assert not errors, errors
