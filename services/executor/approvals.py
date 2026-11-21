"""Approval requests and decisions for runbook steps (spec §6 "APPROVE / REJECT", §7, §11).

- `request_approval` is the executor's `approval_requested` hook: it creates the `approvals` row with its own code
  and queues `kind = approval` notifications (SMS + email) to everyone with the required role, in the executor's
  transaction.
- `decide` (UI / email link) and `decide_by_code` (SMS) check the role, record who decided and how, and let the
  step run (approve) or end the run (reject). A code identifies exactly one request.
- Email links carry a signed token (HMAC, 24 h) naming the approval and the person it was emailed to; the page behind
  it (`/approvals/by-token/{token}`, no login, contracts/openapi.yaml) lets that person approve or reject, still with
  the role check. A decided approval cannot be decided again, so a link works once.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg

from db.rows import plain_rows
from services.delivery.codes import CODE_ALPHABET, CODE_LENGTH, new_code  # noqa: F401 - re-exported
from services.executor.engine import Executor, ExecutorError

APPROVAL_CHANNELS = ("sms", "email")
TOKEN_TTL = timedelta(hours=24)


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


@plain_rows
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
    result: str  # approved | rejected | unknown_code | not_allowed | already_decided | invalid_token
    approval_id: int | None = None


def _may_decide(conn: psycopg.Connection, user: str, team: str, role: str) -> bool:
    return user in approvers(conn, team, role)


@plain_rows
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


@plain_rows
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


# --- agreed entry points for the API (docs/plans/c-api-web.md, agreements 4 and 5) ---------------------

_DECISIONS = {"approve": "approved", "approved": "approved", "reject": "rejected", "rejected": "rejected"}


@plain_rows
def decide_approval(
    conn: psycopg.Connection,
    approval_id: int,
    actor: str,
    decision: str,
    via: str,
    now: datetime | None = None,
    executor: Executor | None = None,
) -> Decision:
    """Agreement 4. `decision` is `approve` / `reject` (API wording) or `approved` / `rejected`.
    Results: approved | rejected | unknown_code (404) | not_allowed (403) | already_decided (409)."""
    if decision not in _DECISIONS:
        raise ValueError(f"bad decision {decision!r}")
    return decide(conn, approval_id, _DECISIONS[decision], actor, via, now or datetime.now(UTC), executor)


@plain_rows
def decide_by_token(
    conn: psycopg.Connection, token: str, decision: str, now: datetime | None = None, executor: Executor | None = None
) -> Decision:
    """`POST /approvals/by-token/{token}` (no login): the token names the approval and the person it was emailed
    to; their role is still checked. Result `invalid_token` for a bad or expired token (410 / 404)."""
    now = now or datetime.now(UTC)
    verified = verify_approval_token(token, now)
    if verified is None:
        return Decision("invalid_token")
    approval_id, user = verified
    return decide_approval(conn, approval_id, user, decision, "email", now, executor)


# --- signed email links (agreement 5) ------------------------------------------------------------------


def _secret() -> bytes:
    secret = os.environ.get("APPROVAL_LINK_SECRET")
    if not secret:
        raise RuntimeError("APPROVAL_LINK_SECRET is not set")
    return secret.encode()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign_approval_token(approval_id: int, user_id: str, now: datetime, secret: bytes | None = None) -> str:
    """HMAC-SHA256 token for the email link of one approval request, addressed to one person, valid 24 h.
    Single use: a decided approval cannot be decided again."""
    body = _b64(json.dumps({"a": approval_id, "u": user_id, "exp": int((now + TOKEN_TTL).timestamp())}).encode())
    mac = _b64(hmac.new(secret or _secret(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{mac}"


def verify_approval_token(token: str, now: datetime, secret: bytes | None = None) -> tuple[int, str] | None:
    """(approval_id, user_id) for a valid, unexpired token; None otherwise."""
    try:
        body, mac = token.split(".")
        expected = _b64(hmac.new(secret or _secret(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(mac, expected):
            return None
        data = json.loads(_unb64(body))
        if now.timestamp() > data["exp"]:
            return None
        return int(data["a"]), str(data["u"])
    except (ValueError, KeyError, TypeError):
        return None
