"""Runbook executor (spec §7) and scheduler jobs (spec §5)."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from contracts.actions import ActionResult, Approval
from db.testing import TEST_DATABASE_URL, temporary_database
from services.executor import scheduler
from services.executor.engine import LEASE, MAX_STEP_RUNS, RETRY_DELAY, Executor, ExecutorError, Hooks, parse_duration
from services.executor.templating import lookup, render, render_params

T0 = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)

LETTER = {
    "$service": "order-settlement",
    "$severity": "critical",
    "payload": {
        "type": "WORKFLOW_FAILED",
        "workflowId": "wf-42",
        "error": {"code": "TIMEOUT", "message": "counterparty timeout"},
        "labels": {"app.kubernetes.io/name": "settler"},
        "steps": [{"name": "settle"}],
    },
}

# --- templating (pure) ---------------------------------------------------------------------------------


def test_lookup_paths():
    payload = LETTER["payload"]
    assert lookup(payload, "error.code") == "TIMEOUT"
    assert lookup(payload, 'labels["app.kubernetes.io/name"]') == "settler"
    assert lookup(payload, "steps.0.name") == "settle"
    assert lookup(payload, "steps.5.name") is None
    assert lookup(payload, "error.code.deeper") is None


def test_render_params():
    params = {"workflowId": "{{workflowId}}", "note": "{{ $service }}: {{error.message}} {{missing}}", "n": 3}
    assert render_params(params, LETTER) == {
        "workflowId": "wf-42",
        "note": "order-settlement: counterparty timeout —",
        "n": 3,
    }
    assert render("{{__class__}}", LETTER) == "—"  # no attribute access, no code


def test_parse_duration():
    assert parse_duration("2m") == timedelta(minutes=2)
    with pytest.raises(ValueError):
        parse_duration("2 minutes")


# --- fake actions --------------------------------------------------------------------------------------


class FakeAction:
    def __init__(self, name, min_approval=Approval.NONE, results=None):
        self.name = name
        self.params_schema = {"type": "object"}
        self.min_approval = min_approval
        self.results = list(results or [])
        self.calls = []

    async def execute(self, params, ctx):
        self.calls.append((params, ctx))
        result = self.results.pop(0) if self.results else ActionResult(ok=True, output={"status": 200})
        if isinstance(result, Exception):
            raise result
        return result


FAIL = ActionResult(ok=False, error="mock workflow engine: 500")


def actions(*items):
    return {a.name: a for a in items}


SPEC_RUNBOOK = {  # spec §7, as imported
    "id": "settlement-timeout",
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


# --- database fixtures ---------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def database():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    with temporary_database("test_executor", migrate=True) as conninfo:
        yield conninfo


@pytest.fixture
def conn(database):
    with psycopg.connect(database, autocommit=True) as conn:
        conn.execute("TRUNCATE runbook_runs, step_runs, users, audit_log CASCADE")
        conn.execute("DELETE FROM alerts")
        conn.execute("DELETE FROM processed_events")
        conn.execute("INSERT INTO users (id, name) VALUES ('dave', 'Dave'), ('carol', 'Carol')")
        conn.execute(
            "INSERT INTO alerts (id, owner, severity, first_event) VALUES (1, %s, 'critical', %s)",
            (Jsonb({"team": "settlements"}), Jsonb(LETTER)),
        )
        yield conn


def steps(conn, run_id):
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM step_runs WHERE run_id = %s ORDER BY seq", (run_id,))
        return cur.fetchall()


def run_status(conn, run_id):
    return conn.execute("SELECT status FROM runbook_runs WHERE id = %s", (run_id,)).fetchone()[0]


def alert_state(conn):
    return conn.execute("SELECT state, resolved_how, resolved_by FROM alerts WHERE id = 1").fetchone()


def history(conn, alert_id=1):
    rows = conn.execute(
        "SELECT actor, action, details FROM audit_log WHERE target_type = 'alert' AND target_id = %s ORDER BY id",
        (str(alert_id),),
    ).fetchall()
    return [(actor, action, details.get("step")) for actor, action, details in rows]


def drain(executor, conn, now, rounds=20):
    """Run the executor until nothing is due at `now`. Returns (step_id, outcome) in order."""
    outcomes = []
    for _ in range(rounds):
        rows = executor.claim(conn, now)
        if not rows:
            return outcomes
        for row in rows:
            outcomes.append((row["step_id"], asyncio.run(executor.process(conn, row, now))))
    return outcomes


def start(executor, conn, runbook, mode=None, by=None, now=T0):
    with conn.transaction():
        return executor.start_run(conn, 1, runbook, now, started_by=by, mode=mode)


# --- spec §13 test 12 (runbook part) ------------------------------------------------------------------


@pytest.mark.db
def test_spec_runbook_health_check_two_failed_retries_then_approved_escalation(conn):
    http_get = FakeAction("http_get")
    retry = FakeAction("retry_workflow", results=[FAIL, FAIL])
    escalate = FakeAction("escalate")
    requested = []
    executor = Executor(
        actions(http_get, retry, escalate),
        hooks=Hooks(approval_requested=lambda c, step_run_id, run, step: requested.append((step_run_id, step["id"]))),
    )
    run_id = start(executor, conn, SPEC_RUNBOOK)

    assert drain(executor, conn, T0) == [
        ("check-counterparty", "succeeded"),
        ("retry", "retry"),
    ]
    assert drain(executor, conn, T0 + RETRY_DELAY) == [("retry", "failed"), ("escalate", "waiting for approval")]
    assert run_status(conn, run_id) == "waiting_approval"
    assert escalate.calls == []
    [(escalate_run_id, requested_step)] = requested
    assert requested_step == "escalate"

    with conn.transaction():
        executor.approve(conn, escalate_run_id, "carol", T0 + timedelta(minutes=1))
    assert drain(executor, conn, T0 + timedelta(minutes=1)) == [("escalate", "succeeded")]

    timeline = [(s["step_id"], s["status"], s["attempt"], s["approved_by"]) for s in steps(conn, run_id)]
    assert timeline == [
        ("check-counterparty", "succeeded", 1, None),
        ("retry", "failed", 2, None),
        ("escalate", "succeeded", 1, "carol"),
    ]
    assert retry.calls[0][0] == {"workflowId": "wf-42"}  # params rendered from the event
    assert steps(conn, run_id)[1]["params"] == {"workflowId": "wf-42"}
    assert escalate.calls[0][1].approved_by == "carol"
    assert run_status(conn, run_id) == "succeeded"
    assert alert_state(conn)[0] == "open"


@pytest.mark.db
def test_successful_retry_resolves_alert_and_skips_escalation(conn):
    retry = FakeAction("retry_workflow", results=[FAIL])  # second attempt succeeds
    executor = Executor(actions(FakeAction("http_get"), retry, FakeAction("escalate")))
    run_id = start(executor, conn, SPEC_RUNBOOK, by="dave")

    drain(executor, conn, T0)
    assert drain(executor, conn, T0 + RETRY_DELAY) == [("retry", "succeeded, alert resolved")]

    assert [s["step_id"] for s in steps(conn, run_id)] == ["check-counterparty", "retry"]
    assert run_status(conn, run_id) == "succeeded"
    assert alert_state(conn) == ("resolved", "runbook", "dave")
    assert ("dave", "alert.resolved", None) in history(conn)
    assert history(conn)[0] == ("dave", "run.started", None)


@pytest.mark.db
def test_failed_health_check_is_recorded_and_execution_continues(conn):
    """Spec §7: the original does not say what to do on a failed health check -> record it and go on."""
    http_get = FakeAction("http_get", results=[ActionResult(ok=False, error="HTTP 503", output={"status": 503})])
    executor = Executor(actions(http_get, FakeAction("retry_workflow"), FakeAction("escalate")))
    run_id = start(executor, conn, SPEC_RUNBOOK)

    assert drain(executor, conn, T0) == [("check-counterparty", "failed"), ("retry", "succeeded, alert resolved")]
    first = steps(conn, run_id)[0]
    assert (first["status"], first["error"], first["result"]) == ("failed", "HTTP 503", {"status": 503})


# --- modes, people, approvals --------------------------------------------------------------------------


@pytest.mark.db
def test_manual_mode_waits_for_the_run_button(conn):
    http_get = FakeAction("http_get")
    executor = Executor(actions(http_get, FakeAction("retry_workflow"), FakeAction("escalate")))
    run_id = start(executor, conn, {**SPEC_RUNBOOK, "mode": "manual"}, by="dave")

    assert drain(executor, conn, T0 + timedelta(hours=1)) == []  # nothing runs by itself
    [first] = steps(conn, run_id)
    with conn.transaction():
        executor.run_step(conn, first["id"], "dave", T0)
    assert drain(executor, conn, T0) == [("check-counterparty", "succeeded")]
    assert steps(conn, run_id)[0]["started_by"] == "dave"
    assert http_get.calls[0][1].started_by == "dave"
    with pytest.raises(ExecutorError):
        executor.run_step(conn, first["id"], "dave", T0)


@pytest.mark.db
def test_runbook_cannot_lower_the_actions_minimum_approval(conn):
    restart = FakeAction("restart_service", min_approval=Approval.REQUIRED)
    runbook = {"id": "rb", "mode": "auto", "steps": [{"id": "restart", "kind": "action", "action": "restart_service",
                                                      "approval": "none"}]}  # fmt: skip
    executor = Executor(actions(restart))
    start(executor, conn, runbook)

    assert drain(executor, conn, T0) == [("restart", "waiting for approval")]
    assert restart.calls == []


@pytest.mark.db
def test_reject_ends_the_run(conn):
    executor = Executor(actions(FakeAction("http_get"), FakeAction("retry_workflow", results=[FAIL, FAIL]),
                                FakeAction("escalate")))  # fmt: skip
    run_id = start(executor, conn, SPEC_RUNBOOK)
    drain(executor, conn, T0)
    drain(executor, conn, T0 + RETRY_DELAY)
    waiting = steps(conn, run_id)[-1]

    with conn.transaction():
        executor.reject(conn, waiting["id"], "carol", T0)
    assert run_status(conn, run_id) == "rejected"
    assert steps(conn, run_id)[-1]["status"] == "rejected"
    with pytest.raises(ExecutorError):
        executor.approve(conn, waiting["id"], "carol", T0)  # one decision per request


# --- manual steps, waits, only_if_open -----------------------------------------------------------------

CHECKLIST = {
    "id": "pricing-latency",
    "mode": "auto",
    "steps": [
        {"id": "dash", "kind": "text", "text": "Check the dashboard for {{$service}}"},
        {"id": "logs", "kind": "text", "text": "Read the logs"},
        {"id": "wait", "kind": "wait", "duration": "2m"},
        {"id": "notify", "kind": "action", "action": "notify", "only_if_open": True},
    ],
}


@pytest.mark.db
def test_checklist_records_who_and_when_and_does_not_block(conn):
    """Spec §13 test 13: manual steps; ticking records the person and the time."""
    executor = Executor(actions(FakeAction("notify")))
    run_id = start(executor, conn, CHECKLIST)
    created = steps(conn, run_id)
    assert [(s["step_id"], s["status"]) for s in created] == [
        ("dash", "pending"),
        ("logs", "pending"),
        ("wait", "scheduled"),  # manual steps did not block the wait
    ]

    with conn.transaction():
        executor.mark_done(conn, created[1]["id"], "dave", T0 + timedelta(seconds=30))  # any order
    with pytest.raises(ExecutorError):
        executor.mark_done(conn, created[1]["id"], "carol", T0)
    ticked = steps(conn, run_id)[1]
    assert (ticked["status"], ticked["executed_by"], ticked["finished_at"]) == (
        "done",
        "dave",
        T0 + timedelta(seconds=30),
    )

    assert drain(executor, conn, T0 + timedelta(minutes=1)) == []  # wait not due yet
    assert drain(executor, conn, T0 + timedelta(minutes=2)) == [("wait", "waited"), ("notify", "succeeded")]
    assert run_status(conn, run_id) == "running"  # "dash" is still open

    with conn.transaction():
        executor.mark_done(conn, created[0]["id"], "carol", T0 + timedelta(minutes=3))
    assert run_status(conn, run_id) == "succeeded"
    done = [(actor, step) for actor, action, step in history(conn) if action == "step.done"]
    assert done == [("dave", "logs"), ("carol", "dash")]  # spec §13 test 13: who ticked what


@pytest.mark.db
def test_only_if_open_skips_when_alert_resolved(conn):
    notify = FakeAction("notify")
    executor = Executor(actions(notify))
    run_id = start(executor, conn, CHECKLIST)
    conn.execute("UPDATE alerts SET state = 'resolved' WHERE id = 1")

    outcomes = drain(executor, conn, T0 + timedelta(minutes=2))
    assert outcomes == [("wait", "waited"), ("notify", "skipped (alert resolved)")]
    assert notify.calls == []
    assert steps(conn, run_id)[-1]["status"] == "skipped"


# --- robustness ----------------------------------------------------------------------------------------


@pytest.mark.db
def test_expired_lease_is_taken_over_and_stale_result_ignored(conn):
    http_get = FakeAction("http_get")
    executor = Executor(actions(http_get, FakeAction("retry_workflow"), FakeAction("escalate")))
    start(executor, conn, SPEC_RUNBOOK)

    [stale] = executor.claim(conn, T0)  # this executor "dies"
    assert executor.claim(conn, T0 + LEASE - timedelta(seconds=1)) == []
    [fresh] = executor.claim(conn, T0 + LEASE + timedelta(seconds=1))
    assert fresh["id"] == stale["id"]

    assert asyncio.run(executor.process(conn, stale, T0)) == "lost lease"
    assert asyncio.run(executor.process(conn, fresh, T0 + LEASE)) == "succeeded"


@pytest.mark.db
def test_unknown_action_and_exceptions_fail_the_step(conn):
    runbook = {"id": "rb", "mode": "auto", "steps": [
        {"id": "boom", "kind": "action", "action": "explodes"},
        {"id": "ghost", "kind": "action", "action": "does_not_exist"},
    ]}  # fmt: skip
    explodes = FakeAction("explodes", results=[RuntimeError("bug")])
    executor = Executor(actions(explodes))
    run_id = start(executor, conn, runbook)

    # an unknown action is treated as "approval required": it never runs by itself
    assert drain(executor, conn, T0) == [("boom", "failed"), ("ghost", "waiting for approval")]
    assert steps(conn, run_id)[0]["error"] == "RuntimeError: bug"


@pytest.mark.db
def test_on_failure_loop_is_stopped(conn):
    runbook = {"id": "rb", "mode": "auto", "steps": [
        {"id": "again", "kind": "action", "action": "flaky", "on_failure": "again"},
    ]}  # fmt: skip
    executor = Executor(actions(FakeAction("flaky", results=[FAIL] * (MAX_STEP_RUNS + 5))))
    run_id = start(executor, conn, runbook)
    drain(executor, conn, T0, rounds=MAX_STEP_RUNS + 10)
    assert run_status(conn, run_id) == "failed"
    assert len(steps(conn, run_id)) == MAX_STEP_RUNS


@pytest.mark.db
def test_cancel_run(conn):
    executor = Executor(actions(FakeAction("notify")))
    run_id = start(executor, conn, CHECKLIST)
    with conn.transaction():
        executor.cancel_run(conn, run_id, T0)
    assert run_status(conn, run_id) == "cancelled"
    assert {s["status"] for s in steps(conn, run_id)} == {"skipped"}
    assert drain(executor, conn, T0 + timedelta(hours=1)) == []


# --- scheduler -----------------------------------------------------------------------------------------


@pytest.mark.db
def test_silence_resolves_alert_after_auto_resolve_after(conn, database):
    """Spec §13 test 16: a pricing alert without new events for autoResolveAfter closes itself."""
    conn.execute(
        "INSERT INTO alerts (id, last_seen, auto_resolve_after) VALUES"
        " (10, %(t)s - interval '31 minutes', '30 minutes'),"  # silent long enough
        " (13, %(t)s - interval '40 minutes', '30 minutes'),"
        " (11, %(t)s - interval '29 minutes', '30 minutes'),"  # not yet
        " (12, %(t)s - interval '2 days', NULL)",  # no autoResolveAfter
        {"t": T0},
    )
    with psycopg.connect(database, autocommit=True) as listener:
        listener.execute("LISTEN alerts_changed")
        assert scheduler.resolve_silent_alerts(conn, T0) == [10, 13]
        payloads = [json.loads(n.payload) for n in listener.notifies(timeout=1, stop_after=1)]
    assert payloads == [{"ids": [10, 13]}]  # one message, ids only
    states = dict(conn.execute("SELECT id, coalesce(resolved_how, state) FROM alerts WHERE id >= 10").fetchall())
    assert states == {10: "silence", 11: "open", 12: "open", 13: "silence"}
    assert history(conn, 10) == [("scheduler", "alert.resolved", None)]
    assert scheduler.resolve_silent_alerts(conn, T0) == []  # idempotent


@pytest.mark.db
def test_processed_events_cleanup(conn):
    conn.execute(
        "INSERT INTO processed_events VALUES ('old', %(t)s - interval '25 hours'), ('new', %(t)s - interval '1 hour')",
        {"t": T0},
    )
    assert scheduler.clean_processed_events(conn, T0) == 1
    assert [r[0] for r in conn.execute("SELECT event_id FROM processed_events").fetchall()] == ["new"]


@pytest.mark.db
def test_jobs_on_a_fresh_database():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    with temporary_database("test_sched_empty", migrate=True) as conninfo:
        with psycopg.connect(conninfo, autocommit=True) as c:
            assert scheduler.run_jobs(c, T0) == {"silence": 0, "processed_events": 0}


def test_notify_payload_uses_the_contract_split():
    from contracts.notify import MAX_IDS_PER_MESSAGE
    from services.executor.alerts import notify_alerts_changed

    class Recorder:
        def __init__(self):
            self.payloads = []

        def execute(self, sql, params):
            self.payloads.append(params[-1])

    conn = Recorder()
    big = [10**18 + i for i in range(MAX_IDS_PER_MESSAGE * 2 + 1)]  # 19-digit ids
    notify_alerts_changed(conn, big)
    assert [len(json.loads(p)["ids"]) for p in conn.payloads] == [MAX_IDS_PER_MESSAGE, MAX_IDS_PER_MESSAGE, 1]
    assert all(len(p.encode()) < 8000 for p in conn.payloads)  # Postgres NOTIFY limit
    notify_alerts_changed(conn, [])
    assert len(conn.payloads) == 3  # nothing changed -> no message
