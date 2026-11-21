"""Runbooks, runbook runs and approvals over HTTP (contracts/openapi.yaml; spec §7, §9 P2, §13 tests 12 and 13)."""

import asyncio
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg.types.json import Jsonb

from services.api.routers.runbooks import actions
from services.api.tests.helpers import assert_matches, insert_alert
from services.executor import runbooks as rb
from services.executor.approvals import request_approval, sign_approval_token
from services.executor.engine import Executor, Hooks

RUNBOOK = {
    "id": "rates-restart",
    "owner": {"team": "rates"},
    "format": "yaml",
    "mode": "manual",
    "steps": [
        {"id": "look", "kind": "text", "text": "Check workflow {{workflowId}} in the console"},
        {"id": "restart", "kind": "action", "action": "restart_service", "params": {"service": "{{$service}}"}},
        {
            "id": "health",
            "kind": "action",
            "action": "http_get",
            "params": {"url": "http://counterparty-service/health"},
        },
    ],
}


@pytest.fixture
def runbooks(conn, monkeypatch):
    monkeypatch.setenv("APPROVAL_LINK_SECRET", "test-link-secret")
    conn.execute("TRUNCATE runbooks, runbook_runs, step_runs, approvals CASCADE")

    def add(runbook=RUNBOOK, approved_by="marek", rule_id="rates-rule"):
        rb.upsert(conn, runbook, owner_team=runbook["owner"]["team"], title="Restart", fmt=runbook["format"],
                  original="...", approved_by=approved_by, reset=True)  # fmt: skip
        conn.execute(
            """INSERT INTO rules (id, owner, "when", runbook_ids, updated_by) VALUES (%s, %s, %s, %s, 'test')
               ON CONFLICT (id) DO UPDATE SET runbook_ids = rules.runbook_ids || EXCLUDED.runbook_ids""",
            (rule_id, Jsonb({"team": "rates"}), Jsonb({"all": []}), [runbook["id"]]),
        )

    return add


def rates_alert(conn, title="Restart needed"):
    return insert_alert(conn, owner={"team": "rates"}, rule_id="rates-rule", title=title)


# --- runbooks ------------------------------------------------------------------------------------------


def test_read_runbooks(client, login, runbooks):
    runbooks()
    headers = login("anna")
    [listed] = client.get("/api/runbooks", headers=headers).json()
    assert_matches("Runbook", listed)
    assert (listed["id"], listed["approvedBy"], listed["owner"]) == ("rates-restart", "marek", {"team": "rates"})
    assert client.get("/api/runbooks?team=pricing", headers=headers).json() == []
    assert client.get("/api/runbooks/nope", headers=headers).status_code == 404


def test_edit_clears_approval_and_needs_manager(client, login, runbooks):
    runbooks()
    body = {"title": "Restart v2", "steps": RUNBOOK["steps"][:2]}
    assert client.put("/api/runbooks/rates-restart", json=body, headers=login("mia")).status_code == 403
    edited = client.put("/api/runbooks/rates-restart", json=body, headers=login("marek")).json()
    assert (edited["title"], edited["approvedBy"], len(edited["canonical"]["steps"])) == ("Restart v2", None, 2)

    bad = {"title": "x", "steps": [{"id": "a", "kind": "action"}]}  # action step without `action`
    response = client.put("/api/runbooks/rates-restart", json=bad, headers=login("marek"))
    assert response.status_code == 422
    assert response.json()["error"]["details"]

    approved = client.post("/api/runbooks/rates-restart/approve", headers=login("marek")).json()
    assert approved["approvedBy"] == "marek"
    auto = client.patch("/api/runbooks/rates-restart", json={"mode": "auto"}, headers=login("marek")).json()
    assert (auto["mode"], auto["canonical"]["mode"], auto["approvedBy"]) == ("auto", "auto", "marek")
    assert client.delete("/api/runbooks/rates-restart", headers=login("mia")).status_code == 403
    assert client.delete("/api/runbooks/rates-restart", headers=login("marek")).status_code == 204


def test_actions_registry(client, login):
    actions = client.get("/api/actions", headers=login("anna")).json()
    for action in actions:
        assert_matches("ActionInfo", action)
    levels = {a["name"]: a["minApproval"] for a in actions}
    assert levels["restart_service"] == "required"
    assert levels["http_get"] == "none"


def test_alert_detail_shows_rendered_runbooks(client, login, conn, runbooks):
    runbooks()
    alert_id = rates_alert(conn)
    [runbook] = client.get(f"/api/alerts/{alert_id}", headers=login("anna")).json()["runbooks"]
    assert_matches("AlertRunbook", runbook)
    assert (runbook["approved"], runbook["runId"]) == (True, None)
    look, restart, health = runbook["steps"]
    assert look["text"] == "Check workflow wf-123 in the console"  # value from the first event
    assert restart["params"] == {"service": "order-settlement"}
    assert (restart["effectiveApproval"], health["effectiveApproval"]) == ("required", "none")


# --- runs (spec §13 test 13 + the approval part of test 12) -------------------------------------------


def worker_pass(database):
    """One executor pass on its own connection, like the executor process."""
    executor = Executor(dict(actions()), hooks=Hooks(approval_requested=request_approval))
    now = datetime.now(UTC)
    with psycopg.connect(database, autocommit=True) as worker:
        for row in executor.claim(worker, now):
            asyncio.run(executor.process(worker, row, now))


def test_run_a_runbook_from_the_ui(client, login, conn, runbooks, database):
    runbooks()
    alert_id = rates_alert(conn)
    start = f"/api/alerts/{alert_id}/runbook-runs"

    assert client.post(start, json={"runbookId": "rates-restart"}, headers=login("anna")).status_code == 403
    run = client.post(start, json={"runbookId": "rates-restart"}, headers=login("mia"))
    assert run.status_code == 201, run.text
    run = run.json()
    assert_matches("RunbookRun", run)
    assert (run["startedBy"], run["status"]) == ("mia", "running")
    look, restart, health = run["steps"]
    assert (look["status"], restart["status"], health["id"]) == ("pending", "pending", None)  # health not reached
    assert client.post(start, json={"runbookId": "rates-restart"}, headers=login("mia")).status_code == 409

    steps = f"/api/runbook-runs/{run['id']}/steps"
    done = client.post(f"{steps}/look/done", json={"note": "console is green"}, headers=login("mia")).json()
    assert_matches("StepRun", done)
    assert (done["status"], done["executedBy"], done["note"]) == ("done", "mia", "console is green")
    assert client.post(f"{steps}/look/done", headers=login("mia")).status_code == 409
    assert client.post(f"{steps}/health/start", headers=login("mia")).status_code == 409  # not reached yet

    started = client.post(f"{steps}/restart/start", headers=login("mia"))
    assert started.status_code == 202
    assert (started.json()["startedBy"], started.json()["nextRunAt"] is not None) == ("mia", True)

    worker_pass(database)  # restart_service needs approval -> waiting_approval + approval request
    restart = client.get(f"/api/runbook-runs/{run['id']}", headers=login("anna")).json()["steps"][1]
    assert restart["status"] == "waiting_approval"
    approval_id = restart["approvalId"]

    mine = client.get("/api/approvals?status=pending&mine=true", headers=login("marek")).json()
    assert [a["id"] for a in mine] == [approval_id]
    assert_matches("Approval", mine[0])
    assert client.get("/api/approvals?status=pending&mine=true", headers=login("mia")).json() == []

    assert client.post(f"/api/approvals/{approval_id}/approve", headers=login("mia")).status_code == 403
    approved = client.post(f"/api/approvals/{approval_id}/approve", headers=login("marek")).json()
    assert (approved["status"], approved["decidedBy"], approved["via"]) == ("approved", "marek", "ui")
    assert client.post(f"/api/approvals/{approval_id}/reject", headers=login("marek")).status_code == 409
    assert client.post("/api/approvals/999999/approve", headers=login("marek")).status_code == 404


def test_unapproved_runbook_cannot_run(client, login, conn, runbooks):
    runbooks(approved_by=None)
    alert_id = rates_alert(conn)
    response = client.post(f"/api/alerts/{alert_id}/runbook-runs", json={"runbookId": "rates-restart"},
                           headers=login("mia"))  # fmt: skip
    assert response.status_code == 409
    assert "approved" in response.json()["error"]["message"]


def test_email_link(client, login, conn, runbooks, database):
    """Spec §6: email approve / reject link, signed, single use, 24 h, no login."""
    runbooks()
    alert_id = rates_alert(conn)
    run_id = client.post(f"/api/alerts/{alert_id}/runbook-runs", json={"runbookId": "rates-restart"},
                         headers=login("mia")).json()["id"]  # fmt: skip
    client.post(f"/api/runbook-runs/{run_id}/steps/restart/start", headers=login("mia"))
    worker_pass(database)
    approval_id = client.get(f"/api/runbook-runs/{run_id}", headers=login("mia")).json()["steps"][1]["approvalId"]
    now = datetime.now(UTC)

    token = sign_approval_token(approval_id, "marek", now)
    page = client.get(f"/api/approvals/by-token/{token}").json()  # no Authorization header
    assert_matches("ApprovalByToken", page)
    assert (page["userId"], page["step"]["id"], page["alert"]["id"]) == ("marek", "restart", alert_id)

    member_token = sign_approval_token(approval_id, "mia", now)
    assert client.post(f"/api/approvals/by-token/{member_token}", json={"decision": "approve"}).status_code == 403
    expired = sign_approval_token(approval_id, "marek", now - timedelta(days=2))
    assert client.get(f"/api/approvals/by-token/{expired}").status_code == 410
    assert client.get("/api/approvals/by-token/not-a-valid-token-at-all").status_code == 404

    decided = client.post(f"/api/approvals/by-token/{token}", json={"decision": "reject"}).json()
    assert (decided["status"], decided["decidedBy"], decided["via"]) == ("rejected", "marek", "email")
    assert client.post(f"/api/approvals/by-token/{token}", json={"decision": "approve"}).status_code == 409
    assert client.get(f"/api/approvals/by-token/{token}").status_code == 409
    assert client.get(f"/api/runbook-runs/{run_id}", headers=login("mia")).json()["status"] == "rejected"
