"""Approval requests and decisions for runbook steps (spec §6 "APPROVE / REJECT", §7, §11).

- `request_approval` is the executor's `approval_requested` hook: it creates the `approvals` row with its own code
  and queues `kind = approval` notifications (SMS + email) to everyone with the required role, in the executor's
  transaction.
- `decide` (UI / email link) and `decide_by_code` (SMS) check the role, record who decided and how, and let the
  step run (approve) or end the run (reject). A code identifies exactly one request.
- Email links carry a signed token (HMAC, expiry) naming the approval and the decision; the UI page shows it to the
  logged-in user and calls `decide`. A decided approval cannot be decided again, so a link works once.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import psycopg

from services.executor.engine import Executor, ExecutorError

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I (contracts/delivery.md)
CODE_LENGTH = 6
APPROVAL_CHANNELS = ("sms", "email")
TOKEN_TTL = timedelta(hours=24)


def new_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def approval_team(run: Mapping[str, Any], alert_owner: Mapping[str, Any] | None) -> str | None:
    """The runbook owner's team, else the alert owner's team."""
    return (run["runbook"].get("owner") or {}).get("team") or (alert_owner or {}).get("team")


def approvers(conn: psycopg.Connection, team: str, role: str) -> list[str]:
    """People allowed to decide: managers for `manager`; every team member for `member`."""
    rows = conn.execute(
        "SELECT user_id FROM team_members WHERE team_id = %s AND (%s = 'member' OR role = 'manager') ORDER BY user_id",
        (team, role),
    ).fetchall()
    return [r[0] for r in rows]


def request_approval(
    conn: psycopg.Connection, step_run_id: int, run: Mapping[str, Any], step: Mapping[str, Any]
) -> int:
    """Executor hook: approvals row + notifications. Returns the approval id."""
    owner = conn.execute("SELECT owner FROM alerts WHERE id = %s", (run["alert_id"],)).fetchone()
    team = approval_team(run, owner[0] if owner else None) or ""
    role = step.get("approval_role", "manager")
    for _ in range(5):  # a code collision with another open request is retried with a new code
        try:
            with conn.transaction():
                approval_id = conn.execute(
                    """INSERT INTO approvals (step_run_id, team_id, required_role, code)
                       VALUES (%s, %s, %s, %s) RETURNING id""",
                    (step_run_id, team, role, code := new_code()),
                ).fetchone()[0]
            break
        except psycopg.errors.UniqueViolation:
            continue
    else:  # pragma: no cover - 32^6 codes
        raise RuntimeError("could not allocate an approval code")

    for user in approvers(conn, team, role):
        for channel in APPROVAL_CHANNELS:
            conn.execute(
                """INSERT INTO notifications (alert_id, user_id, channel, kind, dedup_key, reply_code)
                   VALUES (%s, %s, %s, 'approval', %s, %s)
                   ON CONFLICT (user_id, channel, dedup_key) DO NOTHING""",
                (run["alert_id"], user, channel, f"approval:{approval_id}", code),
            )
    return approval_id


@dataclass(frozen=True)
class Decision:
    result: str  # approved | rejected | unknown_code | not_allowed | already_decided
    approval_id: int | None = None


def _may_decide(conn: psycopg.Connection, user: str, team: str, role: str) -> bool:
    return user in approvers(conn, team, role)


def decide(
    conn: psycopg.Connection,
    approval_id: int,
    decision: str,
    user: str,
    via: str,
    now: datetime,
    executor: Executor | None = None,
) -> Decision:
    """Record a decision on one approval request, in the caller's transaction."""
    if decision not in ("approved", "rejected"):
        raise ValueError(f"bad decision {decision!r}")
    row = conn.execute(
        "SELECT step_run_id, team_id, required_role, decision FROM approvals WHERE id = %s FOR UPDATE",
        (approval_id,),
    ).fetchone()
    if row is None:
        return Decision("unknown_code")
    step_run_id, team, role, existing = row
    if existing is not None:
        return Decision("already_decided", approval_id)
    if not _may_decide(conn, user, team, role):
        return Decision("not_allowed", approval_id)
    conn.execute(
        "UPDATE approvals SET decision = %s, decided_by = %s, via = %s, decided_at = %s WHERE id = %s",
        (decision, user, via, now, approval_id),
    )
    executor = executor or Executor(actions={})
    try:
        if decision == "approved":
            executor.approve(conn, step_run_id, user, now)
        else:
            executor.reject(conn, step_run_id, user, now)
    except ExecutorError:  # the run was cancelled meanwhile; the decision is still recorded
        pass
    return Decision(decision, approval_id)


def decide_by_code(
    conn: psycopg.Connection, code: str, phone: str, decision: str, now: datetime, executor: Executor | None = None
) -> Decision:
    """`APPROVE <code>` / `REJECT <code>` by SMS: only from the phone of someone with the required role."""
    row = conn.execute(
        "SELECT id FROM approvals WHERE code = %s AND decision IS NULL", (code.strip().upper(),)
    ).fetchone()
    if row is None:
        return Decision("unknown_code")
    user = conn.execute("SELECT id FROM users WHERE phone = %s", (phone,)).fetchone()
    if user is None:
        return Decision("not_allowed", row[0])
    return decide(conn, row[0], decision, user[0], "sms", now, executor)


# --- signed email links --------------------------------------------------------------------------------


def _secret() -> bytes:
    secret = os.environ.get("APPROVAL_LINK_SECRET")
    if not secret:
        raise RuntimeError("APPROVAL_LINK_SECRET is not set")
    return secret.encode()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign_link(approval_id: int, decision: str, now: datetime, secret: bytes | None = None) -> str:
    body = _b64(json.dumps({"a": approval_id, "d": decision, "exp": int((now + TOKEN_TTL).timestamp())}).encode())
    mac = _b64(hmac.new(secret or _secret(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{mac}"


def verify_link(token: str, now: datetime, secret: bytes | None = None) -> tuple[int, str] | None:
    """(approval_id, decision) for a valid, unexpired token; None otherwise."""
    try:
        body, mac = token.split(".")
        expected = _b64(hmac.new(secret or _secret(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(mac, expected):
            return None
        data = json.loads(_unb64(body))
        if now.timestamp() > data["exp"] or data["d"] not in ("approved", "rejected"):
            return None
        return int(data["a"]), data["d"]
    except (ValueError, KeyError, TypeError):
        return None
