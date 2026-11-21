"""Inbound SMS and phone "press 1" (owner B, spec §6 "Dyżury, odpowiedzi i linki", contracts/delivery.md).

The fake gateway forwards every reply to `POST /api/inbound/sms`; the API router calls `handle_inbound` inside its
request transaction, so the state change, the audit entry and the NOTIFY commit together.

- `ACK <code>`: acknowledges the alert the code was sent for, only from the phone of the person it was sent to.
  Uses C's `ack_alert`, so UI, SMS and phone change the state with the same code (plan agreement 3).
- `APPROVE <code>` / `REJECT <code>`: decides exactly one approval request, only from the phone of someone with the
  required role (`services.executor.approvals.decide_by_code`).

Results (contracts/openapi.yaml): acknowledged | approved | rejected | unknown_code | not_allowed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

import psycopg

from db.rows import plain_rows
from services.delivery.codes import CODE_ALPHABET, CODE_LENGTH

COMMAND = re.compile(rf"^\s*(ACK|APPROVE|REJECT)\s+([{CODE_ALPHABET}]{{{CODE_LENGTH}}})\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class Command:
    verb: str  # ACK | APPROVE | REJECT
    code: str


def parse(text: str) -> Command | None:
    """`ack k7q2mx` -> Command("ACK", "K7Q2MX"); anything else -> None. Case and surrounding spaces don't matter."""
    match = COMMAND.match(text.upper())
    return Command(match[1], match[2]) if match else None


def normalize_phone(phone: str) -> str:
    """`+48 500-000-003` -> `+48500000003` (numbers are stored in E.164 without separators)."""
    return re.sub(r"[\s\-().]", "", phone)


@plain_rows
def handle_inbound(conn: psycopg.Connection, sender: str, text: str, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    command = parse(text)
    if command is None:
        return "unknown_code"
    phone = normalize_phone(sender)
    if command.verb == "ACK":
        return acknowledge(conn, command.code, phone)

    from services.executor.approvals import decide_by_code

    decision = "approved" if command.verb == "APPROVE" else "rejected"
    result = decide_by_code(conn, command.code, phone, decision, now).result
    return result if result in ("approved", "rejected", "not_allowed") else "unknown_code"


def acknowledge(conn: psycopg.Connection, code: str, phone: str) -> str:
    from services.api.domain.alerts import AlertNotFound, AlertStateConflict, ack_alert

    sent = conn.execute(
        """SELECT n.alert_id, n.user_id, u.phone FROM notifications n JOIN users u ON u.id = n.user_id
           WHERE n.reply_code = %s AND n.kind = 'alert' ORDER BY n.id DESC""",
        (code,),
    ).fetchall()
    if not sent:
        return "unknown_code"
    mine = [(alert_id, user_id) for alert_id, user_id, user_phone in sent if user_phone == phone]
    if not mine:
        return "not_allowed"  # a valid code, but from someone else's phone
    alert_id, user_id = mine[0]
    try:
        ack_alert(conn, alert_id, user_id, via="sms")
    except AlertNotFound:
        return "unknown_code"
    except AlertStateConflict as exc:
        # Repeating ACK on an acknowledged alert is fine; a resolved alert cannot be acknowledged any more.
        return "acknowledged" if (exc.details or {}).get("state") == "acknowledged" else "not_allowed"
    return "acknowledged"
