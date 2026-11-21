"""POST /api/inbound/sms: ACK / APPROVE / REJECT replies from the gateway (spec §6, §13 test 12).

The route is C's thin wrapper, the logic is B's (services/delivery/inbound.py). The API pool hands out dict-row
connections, so these tests also cover B's code running on them.
"""

import asyncio
from datetime import UTC, datetime

import psycopg
import pytest

from contracts.actions import ActionResult, Approval
from services.api.tests.helpers import insert_alert
from services.delivery.inbound import normalize_phone, parse
from services.executor.approvals import request_approval
from services.executor.engine import Executor, Hooks

ANNA, MIA, MAREK = "+48500000001", "+48500000002", "+48500000003"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("ACK K7Q2MX", ("ACK", "K7Q2MX")),
        ("  ack k7q2mx ", ("ACK", "K7Q2MX")),
        ("Approve ABCDEF", ("APPROVE", "ABCDEF")),
        ("REJECT ABCDEF", ("REJECT", "ABCDEF")),
        ("ACK K7Q2M", None),  # too short
        ("ACK K7Q2M0", None),  # 0 is not in the alphabet
        ("ACK", None),
        ("hello", None),
        ("ACK K7Q2MX please", None),
    ],
)
def test_parse(text, expected):
    command = parse(text)
    assert (command.verb, command.code) == expected if expected else command is None


def test_normalize_phone():
    assert normalize_phone("+48 500-000-001") == ANNA
    assert normalize_phone("(+48) 500.000.001") == ANNA


@pytest.fixture
def phones(conn):
    conn.execute("TRUNCATE runbook_runs, step_runs, approvals CASCADE")
    for user, phone in (("anna", ANNA), ("mia", MIA), ("marek", MAREK)):
        conn.execute("UPDATE users SET phone = %s WHERE id = %s", (phone, user))
    yield
    conn.execute("UPDATE users SET phone = NULL")


def notify_with_code(conn, alert_id, user, code, channel="sms"):
    conn.execute(
        "INSERT INTO notifications (alert_id, user_id, channel, kind, dedup_key, reply_code)"
        " VALUES (%s, %s, %s, 'alert', %s, %s)",
        (alert_id, user, channel, f"evt-{alert_id}", code),
    )


def sms(client, sender, text):
    response = client.post("/api/inbound/sms", json={"from": sender, "text": text, "gatewayMessageId": "1"})
    assert response.status_code == 200, response.text
    return response.json()["result"]


def test_ack_by_sms(client, conn, phones, listener):
    received = listener("alerts_changed")
    alert_id = insert_alert(conn)
    notify_with_code(conn, alert_id, "anna", "K7Q2MX")

    assert sms(client, MIA, "ACK K7Q2MX") == "not_allowed"  # valid code, someone else's phone
    assert conn.execute("SELECT state FROM alerts WHERE id = %s", (alert_id,)).fetchone()["state"] == "open"

    assert sms(client, "+48 500 000 001", "ack k7q2mx") == "acknowledged"
    row = conn.execute("SELECT state, acked_by FROM alerts WHERE id = %s", (alert_id,)).fetchone()
    assert (row["state"], row["acked_by"]) == ("acknowledged", "anna")
    audit = conn.execute(
        "SELECT actor, action, details FROM audit_log WHERE target_id = %s", (str(alert_id),)
    ).fetchall()
    assert [(a["actor"], a["action"], a["details"]) for a in audit] == [("anna", "alert.acknowledged", {"via": "sms"})]
    assert {"ids": [alert_id]} in received()

    assert sms(client, ANNA, "ACK K7Q2MX") == "acknowledged"  # repeating is harmless


def test_ack_edge_cases(client, conn, phones):
    resolved = insert_alert(conn, state="resolved", title="old")
    notify_with_code(conn, resolved, "anna", "ABCDEF")
    assert sms(client, ANNA, "ACK ABCDEF") == "not_allowed"  # a resolved alert cannot be acknowledged
    assert sms(client, ANNA, "ACK ZZZZZZ") == "unknown_code"
    assert sms(client, ANNA, "hello") == "unknown_code"
    assert sms(client, "+48999999999", "ACK ABCDEF") == "not_allowed"  # unknown number


def test_invalid_body(client):
    assert client.post("/api/inbound/sms", json={"text": "ACK ABCDEF"}).status_code == 422


class Restart:
    name = "restart_service"
    params_schema = {"type": "object"}
    min_approval = Approval.REQUIRED

    def __init__(self):
        self.calls = 0

    async def execute(self, params, ctx):
        self.calls += 1
        return ActionResult(ok=True)


def waiting_step(conn, database, executor, title):
    """Start a run on the API's dict-row connection, let the executor (own connection) park it for approval."""
    now = datetime.now(UTC)
    alert_id = insert_alert(conn, owner={"team": "rates"}, title=title)
    runbook = {"id": "restart-pricing", "owner": {"team": "rates"}, "mode": "auto",
               "steps": [{"id": "restart", "kind": "action", "action": "restart_service"}]}  # fmt: skip
    run_id = executor.start_run(conn, alert_id, runbook, now)
    with psycopg.connect(database, autocommit=True) as worker:
        for row in executor.claim(worker, now):
            asyncio.run(executor.process(worker, row, now))
    code = conn.execute(
        "SELECT a.code FROM approvals a JOIN step_runs s ON s.id = a.step_run_id WHERE s.run_id = %s", (run_id,)
    ).fetchone()["code"]
    return run_id, code


def test_approve_and_reject_by_sms(client, conn, phones, database):
    """Spec §13 test 12 (SMS part): `APPROVE <code>` from the manager's phone; a member's phone is refused."""
    restart = Restart()
    executor = Executor({"restart_service": restart}, hooks=Hooks(approval_requested=request_approval))

    run_id, code = waiting_step(conn, database, executor, "first")
    requests = conn.execute(
        "SELECT user_id, channel FROM notifications WHERE kind = 'approval' ORDER BY channel"
    ).fetchall()
    assert [(r["user_id"], r["channel"]) for r in requests] == [("marek", "email"), ("marek", "sms")]

    assert sms(client, MIA, f"APPROVE {code}") == "not_allowed"  # member of rates, not manager
    assert sms(client, MAREK, f"approve {code.lower()}") == "approved"
    assert sms(client, MAREK, f"APPROVE {code}") == "unknown_code"  # a code works once
    step = conn.execute("SELECT status, approved_by FROM step_runs WHERE run_id = %s", (run_id,)).fetchone()
    assert (step["status"], step["approved_by"]) == ("pending", "marek")

    assert restart.calls == 0
    approved_run = run_id

    # the next executor pass (inside waiting_step) runs the approved step; the new one waits for its own code
    run_id, code = waiting_step(conn, database, executor, "second")
    assert restart.calls == 1
    status = conn.execute("SELECT status FROM runbook_runs WHERE id = %s", (approved_run,)).fetchone()["status"]
    assert status == "succeeded"

    assert sms(client, MAREK, f"REJECT {code}") == "rejected"
    assert conn.execute("SELECT status FROM runbook_runs WHERE id = %s", (run_id,)).fetchone()["status"] == "rejected"
    assert restart.calls == 1  # the rejected step never ran
