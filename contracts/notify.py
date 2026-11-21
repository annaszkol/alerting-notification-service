"""NOTIFY channels and payload helpers (contract owned by C, description in notify.md).

Shared by every sender (processor, executor, scheduler, API) so the payload format is defined once.
Call inside the transaction that made the change; Postgres delivers after commit and drops on rollback.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

ALERTS_CHANGED = "alerts_changed"
CONFIG_CHANGED = "config_changed"

MAX_IDS_PER_MESSAGE = 350  # 350 x 19-digit bigint ids ≈ 7 KB, under the 8000-byte NOTIFY limit


def alerts_changed_payloads(ids: Iterable[int]) -> list[str]:
    """Payloads for `alerts_changed`: unique ids, sorted, split into messages of at most 350 ids."""
    unique = sorted(set(ids))
    return [
        json.dumps({"ids": unique[i : i + MAX_IDS_PER_MESSAGE]}, separators=(",", ":"))
        for i in range(0, len(unique), MAX_IDS_PER_MESSAGE)
    ]


def config_changed_payload(version: int) -> str:
    return json.dumps({"version": version}, separators=(",", ":"))


def parse_alerts_changed(payload: str) -> list[int]:
    """Ids from an `alerts_changed` payload; malformed payloads give [] instead of raising."""
    try:
        data: Any = json.loads(payload)
        ids = data["ids"]
        return [i for i in ids if isinstance(i, int) and not isinstance(i, bool)]
    except (ValueError, TypeError, KeyError):
        return []


def notify_alerts_changed(cursor: Any, ids: Iterable[int]) -> None:
    """Send `alerts_changed` with a psycopg (sync) cursor, inside the caller's transaction."""
    for payload in alerts_changed_payloads(ids):
        cursor.execute("SELECT pg_notify(%s, %s)", (ALERTS_CHANGED, payload))
