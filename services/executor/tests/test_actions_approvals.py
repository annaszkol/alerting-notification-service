"""Built-in actions, their mocks, and approvals (spec §6, §7, §13 test 12)."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from contracts.actions import ActionContext, AlertContext, Approval
from contracts.delivery import Channel, NotificationKind
from db.testing import TEST_DATABASE_URL, temporary_database
from mocks.action_targets.app import create_app as create_targets
from services.delivery.worker import Claimed, render
from services.executor.actions.builtin import ActionSettings, HttpGet, RetryWorkflow, Webhook, build_registry
from services.executor.approvals import (
    CODE_ALPHABET,
    CODE_LENGTH,
    decide,
    decide_approval,
    decide_by_code,
    decide_by_token,
    new_code,
    request_approval,
    sign_approval_token,
    verify_approval_token,
)
from services.executor.engine import RETRY_DELAY, Executor, Hooks

T0 = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
SECRET = b"test-secret"
ALLOW = frozenset({"counterparty-service", "webhook-receiver"})
LETTER = {"$service": "order-settlement", "payload": {"workflowId": "wf-42", "error": {"code": "TIMEOUT"}}}


def ctx(**kw):
    defaults = dict(
        alert=AlertContext(id=1, state="open", owner={"team": "settlements"}, severity="critical", letter=LETTER),
        runbook_id="rb", runbook_run_id=1, step_run_id=7, step_id="s", attempt=1,
        started_by=None, approved_by=None, http_allowlist=ALLOW,
    )  # fmt: skip
    return ActionContext(**(defaults | kw))


# --- codes and links (pure) ----------------------------------------------------------------------------


def test_codes_use_the_unambiguous_alphabet():
    codes = {new_code() for _ in range(200)}
    assert all(len(c) == CODE_LENGTH and set(c) <= set(CODE_ALPHABET) for c in codes)
    assert len(codes) > 190


def test_signed_tokens():
    token = sign_approval_token(42, "carol", T0, SECRET)
    assert verify_approval_token(token, T0 + timedelta(hours=23), SECRET) == (42, "carol")
    assert verify_approval_token(token, T0 + timedelta(hours=25), SECRET) is None  # expired
    assert verify_approval_token(token, T0, b"other-secret") is None
    _, mac = token.split(".")
    import base64

    forged = json.dumps({"a": 43, "u": "carol", "exp": 9_999_999_999}).encode()
    forged_body = base64.urlsafe_b64encode(forged).rstrip(b"=").decode()
    assert verify_approval_token(f"{forged_body}.{mac}", T0, SECRET) is None  # changed approval id
    assert verify_approval_token("garbage", T0, SECRET) is None


# --- HTTP actions (against the mock app, in process) ---------------------------------------------------


@pytest.fixture
def targets():
    return create_targets()


def transport(app):
    return httpx.ASGITransport(app=app)


def run(coro):
    return asyncio.run(coro)


def test_http_get_only_calls_allowlisted_hosts(targets):
    action = HttpGet(transport(targets))
    ok = run(action.execute({"url": "http://counterparty-service/health"}, ctx()))
    assert ok.ok and ok.output["status"] == 200

    for url in ("http://169.254.169.254/latest/meta-data", "file:///etc/passwd", "http://evil.test/health"):
        blocked = run(action.execute({"url": url}, ctx()))
        assert not blocked.ok and "allowlist" in blocked.error


def test_blocked_call_never_reaches_the_network():
    sent = []
    action = HttpGet(httpx.MockTransport(lambda r: sent.append(r) or httpx.Response(200)))
    run(action.execute({"url": "http://internal-db:5432/"}, ctx()))
    assert sent == []


def test_failure_modes_of_the_mock(targets):
    retry = RetryWorkflow("http://workflow-engine", transport(targets))

    async def flow():
        async with httpx.AsyncClient(transport=transport(targets), base_url="http://workflow-engine") as c:
            await c.post("/api/fail", json={"target": "workflow", "times": 2})
            return [await retry.execute({"workflowId": "wf-42"}, ctx(attempt=n)) for n in (1, 2, 3)]

    results = run(flow())
    assert [r.ok for r in results] == [False, False, True]  # "two failed retries", then OK
    assert results[0].error == "HTTP 500"


def test_event_values_cannot_reach_other_endpoints(targets):
    """Template values end up in URL paths; they are encoded, so `../` from an event cannot hit another route."""
    retry = RetryWorkflow("http://workflow-engine", transport(targets))
    result = run(retry.execute({"workflowId": "../../services/db/restart"}, ctx()))
    assert not result.ok
    assert [c["target"] for c in run(_calls(targets))] == []  # neither retry nor restart was called


def test_webhook_sends_idempotency_key():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"received": True})

    action = Webhook(httpx.MockTransport(handler))
    assert action.min_approval is Approval.REQUIRED
    result = run(action.execute({"url": "http://webhook-receiver/webhook", "body": {"a": 1}}, ctx(attempt=2)))
    assert result.ok
    assert seen[0].headers["Idempotency-Key"] == "step-run-7-2"
    assert json.loads(seen[0].content) == {"a": 1}


def test_registry_minimum_approvals():
    registry = build_registry("postgresql://unused")
    assert {name: a.min_approval for name, a in registry.items()} == {
        "http_get": Approval.NONE,
        "webhook": Approval.REQUIRED,
        "retry_workflow": Approval.NONE,
        "restart_service": Approval.REQUIRED,
        "open_ticket": Approval.NONE,
        "escalate": Approval.NONE,
        "notify": Approval.NONE,
    }  # spec §7 table
    for action in registry.values():
        assert action.params_schema["type"] == "object"  # the UI builds the form from it


# --- approval messages ---------------------------------------------------------------------------------

ALERT_ROW = {"id": 7, "title": "x" * 300, "body": "Step settle: TIMEOUT", "severity": "critical", "state": "open",
             "count": 1}  # fmt: skip
CAROL = {"id": "carol", "name": "Carol", "email": "carol@example.test", "phone": "+48500000003", "timezone": "UTC"}


def test_approval_sms_keeps_the_code_within_160(monkeypatch):
    row = Claimed(1, 7, "carol", Channel.SMS, NotificationKind.APPROVAL, "approval:5", 0, "K7Q2MX")
    text = render(row, CAROL, ALERT_ROW, "http://ui.test").text
    assert len(text) <= 160
    assert text.startswith("APPROVE K7Q2MX or REJECT K7Q2MX: ")


def test_approval_email_has_a_signed_link_for_its_addressee(monkeypatch):
    monkeypatch.setenv("APPROVAL_LINK_SECRET", "s3cret")
    row = Claimed(1, 7, "carol", Channel.EMAIL, NotificationKind.APPROVAL, "approval:5", 0, "K7Q2MX")
    message = render(row, CAROL, {**ALERT_ROW, "title": "Escalate wf-42"}, "http://ui.test")
    token = message.links["approval"].rsplit("/", 1)[1]
    assert verify_approval_token(token, datetime.now(UTC), b"s3cret") == (5, "carol")
    assert message.links["approval"] in message.text
    assert "APPROVE K7Q2MX" in message.text


def test_notify_message_replaces_the_title():
    row = Claimed(1, 7, "carol", Channel.SMS, NotificationKind.ALERT, "notify:3", 0, None, "latency still high")
    assert render(row, CAROL, ALERT_ROW, "http://ui.test").text == "[CRITICAL] latency still high http://ui.test/a/7"


# --- database ------------------------------------------------------------------------------------------

SPEC_RUNBOOK = {
    "id": "settlement-timeout",
    "owner": {"team": "settlements"},
    "mode": "auto",
    "steps": [
        {"id": "check-counterparty", "kind": "action", "action": "http_get",
         "params": {"url": "http://counterparty-service/health"}, "approval": "none"},
        {"id": "retry", "kind": "action", "action": "retry_workflow", "params": {"workflowId": "{{workflowId}}"},
         "approval": "none", "attempts": 2, "resolves": True, "on_failure": "escalate"},
        {"id": "escalate", "kind": "action", "action": "escalate", "params": {"team": "counterparty-support"},
         "approval": "required"},
    ],
}  # fmt: skip


@pytest.fixture(scope="module")
def database():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    with temporary_database("test_actions", migrate=True) as conninfo:
        with psycopg.connect(conninfo, autocommit=True) as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS alerts (
                       id bigint PRIMARY KEY, state text NOT NULL DEFAULT 'open', owner jsonb, severity text,
                       first_event jsonb, resolved_how text, resolved_by text, resolved_at timestamptz)"""
            )
        yield conninfo


@pytest.fixture
def conn(database):
    with psycopg.connect(database, autocommit=True) as conn:
        conn.execute("TRUNCATE runbook_runs, step_runs, approvals, alert_escalations, channel_preferences,"
                     " teams, users CASCADE")  # fmt: skip
        conn.execute("DELETE FROM alerts")
        conn.execute(
            "INSERT INTO users (id, name, phone) VALUES ('carol', 'Carol', '+48500000003'),"
            " ('dave', 'Dave', '+48500000004'), ('henry', 'Henry', '+48500000008'), ('ivan', 'Ivan', '+48500000009')"
        )
        conn.execute("INSERT INTO teams VALUES ('settlements', 'Settlements'), ('counterparty-support', 'CP')")
        conn.execute(
            "INSERT INTO team_members VALUES ('settlements', 'carol', 'manager'), ('settlements', 'dave', 'member'),"
            " ('counterparty-support', 'henry', 'manager'), ('counterparty-support', 'ivan', 'member')"
        )
        conn.execute("INSERT INTO oncall_schedules (team_id, rotation) VALUES ('counterparty-support', ARRAY['ivan'])")
        conn.execute(
            "INSERT INTO alerts (id, owner, severity, first_event) VALUES"
            " (1, %(o)s, 'critical', %(l)s), (2, %(o)s, 'critical', %(l)s)",
            {"o": Jsonb({"team": "settlements"}), "l": Jsonb(LETTER)},
        )
        yield conn


def executor_for(database, targets):
    return Executor(
        build_registry(database, ActionSettings(), transport(targets)),
        http_allowlist=ALLOW,
        hooks=Hooks(approval_requested=request_approval),
    )


def drain(executor, conn, now):
    outcomes = []
    for _ in range(20):
        rows = executor.claim(conn, now)
        if not rows:
            break
        outcomes += [(r["step_id"], asyncio.run(executor.process(conn, r, now))) for r in rows]
    return outcomes


def waiting_for_approval(executor, conn, targets, alert_id=1):
    """Spec runbook with the workflow engine failing: ends with escalation waiting for approval."""
    with conn.transaction():
        run_id = executor.start_run(conn, alert_id, SPEC_RUNBOOK, T0)
    drain(executor, conn, T0)
    drain(executor, conn, T0 + RETRY_DELAY)
    return run_id


def approval(conn, run_id):
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT a.* FROM approvals a JOIN step_runs s ON s.id = a.step_run_id WHERE s.run_id = %s", (run_id,)
        )
        return cur.fetchone()


@pytest.mark.db
def test_spec_test_12_runbook_with_sms_approval(conn, database, targets):
    """Spec §13 test 12: health check executed, two failed retries (mock set to fail), escalation waits for approval,
    `APPROVE <code>` by SMS executes it; the timeline shows who did what."""
    set_fail(targets, "workflow", -1)
    executor = executor_for(database, targets)
    run_id = waiting_for_approval(executor, conn, targets)

    pending = approval(conn, run_id)
    assert (pending["team_id"], pending["required_role"], pending["decision"]) == ("settlements", "manager", None)
    requests = conn.execute(
        "SELECT user_id, channel, reply_code FROM notifications WHERE kind = 'approval' ORDER BY user_id, channel"
    ).fetchall()
    assert requests == [("carol", "email", pending["code"]), ("carol", "sms", pending["code"])]  # managers only

    assert decide_by_code(conn, pending["code"], "+48500000004", "approved", T0).result == "not_allowed"  # dave
    assert decide_by_code(conn, "ZZZZZZ", "+48500000003", "approved", T0).result == "unknown_code"
    with conn.transaction():
        result = decide_by_code(conn, pending["code"].lower(), "+48500000003", "approved", T0, executor)
    assert result.result == "approved"
    assert drain(executor, conn, T0 + timedelta(minutes=1)) == [("escalate", "succeeded")]

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT step_id, status, attempt, approved_by FROM step_runs WHERE run_id = %s ORDER BY seq", (run_id,)
        )
        timeline = cur.fetchall()
    assert [tuple(t.values()) for t in timeline] == [
        ("check-counterparty", "succeeded", 1, None),
        ("retry", "failed", 2, None),
        ("escalate", "succeeded", 1, "carol"),
    ]
    decided = approval(conn, run_id)
    assert (decided["decision"], decided["decided_by"], decided["via"]) == ("approved", "carol", "sms")
    # escalate: team added to the alert, its on-call person notified
    assert conn.execute("SELECT team_id FROM alert_escalations WHERE alert_id = 1").fetchall() == [
        ("counterparty-support",)
    ]
    notified = conn.execute("SELECT DISTINCT user_id FROM notifications WHERE dedup_key LIKE 'escalate:%'").fetchall()
    assert notified == [("ivan",)]
    calls = [c["target"] for c in run(_calls(targets))]
    assert calls == ["health", "workflow", "workflow"]
    assert decide_by_code(conn, pending["code"], "+48500000003", "approved", T0).result == "unknown_code"  # used


def set_fail(targets, target, times):
    async def go():
        async with httpx.AsyncClient(transport=transport(targets), base_url="http://x") as c:
            await c.post("/api/fail", json={"target": target, "times": times})

    run(go())


async def _calls(targets):
    async with httpx.AsyncClient(transport=transport(targets), base_url="http://x") as c:
        return (await c.get("/api/calls")).json()


@pytest.mark.db
def test_code_of_one_request_does_not_approve_another_step(conn, database, targets):
    set_fail(targets, "workflow", -1)
    executor = executor_for(database, targets)
    first = waiting_for_approval(executor, conn, targets, alert_id=1)
    second = waiting_for_approval(executor, conn, targets, alert_id=2)
    code_first, code_second = approval(conn, first)["code"], approval(conn, second)["code"]
    assert code_first != code_second

    with conn.transaction():
        decide_by_code(conn, code_first, "+48500000003", "approved", T0, executor)

    status = dict(conn.execute("SELECT id, status FROM runbook_runs").fetchall())
    assert status == {first: "running", second: "waiting_approval"}
    assert approval(conn, second)["decision"] is None


@pytest.mark.db
def test_reject_by_sms_ends_the_run(conn, database, targets):
    set_fail(targets, "workflow", -1)
    executor = executor_for(database, targets)
    run_id = waiting_for_approval(executor, conn, targets)
    with conn.transaction():
        assert decide_by_code(conn, approval(conn, run_id)["code"], "+48500000003", "rejected", T0).result == "rejected"
    assert conn.execute("SELECT status FROM runbook_runs WHERE id = %s", (run_id,)).fetchone()[0] == "rejected"
    assert drain(executor, conn, T0 + timedelta(minutes=5)) == []


@pytest.mark.db
def test_ui_decision_checks_role_and_is_final(conn, database, targets):
    set_fail(targets, "workflow", -1)
    executor = executor_for(database, targets)
    run_id = waiting_for_approval(executor, conn, targets)
    approval_id = approval(conn, run_id)["id"]

    assert decide(conn, approval_id, "approved", "dave", "ui", T0).result == "not_allowed"  # member, not manager
    assert decide(conn, approval_id, "approved", "henry", "ui", T0).result == "not_allowed"  # other team's manager
    with conn.transaction():
        assert decide(conn, approval_id, "rejected", "carol", "email", T0).result == "rejected"
    assert decide(conn, approval_id, "approved", "carol", "ui", T0).result == "already_decided"  # links work once
    assert approval(conn, run_id)["via"] == "email"


@pytest.mark.db
def test_email_token_and_agreed_api_names(conn, database, targets, monkeypatch):
    """Agreements 4 and 5 (docs/plans/c-api-web.md): decide_approval with API wording, decide_by_token."""
    monkeypatch.setenv("APPROVAL_LINK_SECRET", "s3cret")
    set_fail(targets, "workflow", -1)
    executor = executor_for(database, targets)
    first = waiting_for_approval(executor, conn, targets, alert_id=1)
    second = waiting_for_approval(executor, conn, targets, alert_id=2)
    first_id, second_id = approval(conn, first)["id"], approval(conn, second)["id"]

    dave_token = sign_approval_token(first_id, "dave", T0)  # emailed to someone without the role
    assert decide_by_token(conn, dave_token, "approve", T0).result == "not_allowed"
    assert decide_by_token(conn, "garbage", "approve", T0).result == "invalid_token"
    with conn.transaction():
        assert decide_by_token(conn, sign_approval_token(first_id, "carol", T0), "reject", T0).result == "rejected"
    assert decide_by_token(conn, sign_approval_token(first_id, "carol", T0), "approve", T0).result == (
        "already_decided"
    )  # single use
    assert approval(conn, first)["via"] == "email"

    with conn.transaction():
        assert decide_approval(conn, second_id, "carol", "approve", "ui", T0).result == "approved"
    with pytest.raises(ValueError):
        decide_approval(conn, second_id, "carol", "maybe", "ui", T0)


@pytest.mark.db
def test_member_role_lets_any_member_approve(conn, database, targets):
    runbook = {"id": "rb", "owner": {"team": "settlements"}, "mode": "auto", "steps": [
        {"id": "restart", "kind": "action", "action": "restart_service", "params": {"service": "pricing-engine-1"},
         "approval_role": "member"},
    ]}  # fmt: skip
    executor = executor_for(database, targets)
    with conn.transaction():
        run_id = executor.start_run(conn, 1, runbook, T0)
    assert drain(executor, conn, T0) == [("restart", "waiting for approval")]  # min approval of restart_service
    with conn.transaction():
        assert decide_by_code(conn, approval(conn, run_id)["code"], "+48500000004", "approved", T0).result == "approved"
    assert drain(executor, conn, T0) == [("restart", "succeeded")]


@pytest.mark.db
def test_escalate_and_notify_are_idempotent(conn, database, targets):
    registry = build_registry(database, ActionSettings(), transport(targets))
    alert_ctx = ctx(step_run_id=99)
    first = run(registry["escalate"].execute({"team": "counterparty-support"}, alert_ctx))
    again = run(registry["escalate"].execute({"team": "counterparty-support"}, alert_ctx))  # repeat after a crash
    assert first.ok and first.output["notified"] == ["ivan"]
    assert again.ok and again.output["notified"] == []
    assert not run(registry["escalate"].execute({"team": "nope"}, alert_ctx)).ok

    note = run(registry["notify"].execute({"to": {"user": "dave"}, "message": "latency still high",
                                           "channels": ["sms"]}, ctx(step_run_id=5)))  # fmt: skip
    assert note.ok and note.output["notified"] == ["dave"]
    assert conn.execute("SELECT channel, message FROM notifications WHERE dedup_key = 'notify:5'").fetchall() == [
        ("sms", "latency still high")
    ]
