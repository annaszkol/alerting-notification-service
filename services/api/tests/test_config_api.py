"""Rule tester and configuration endpoints (contracts/openapi.yaml; spec §3, §4, §6, §9, §13 tests 6, 11, 20)."""

import pytest

from libs.conditions import spec_examples as ex
from services.api.tests.helpers import LETTER, assert_matches, insert_alert

WORKFLOW_MAPPING = {**{k: v for k, v in ex.MAPPINGS[0].items() if k not in ("name",)}, "id": "workflow-event"}
RATES_RULE = {
    "id": "rates-workflows",
    "owner": {"team": "rates"},
    "when": {"all": [{"type": "WORKFLOW_FAILED"}, {"$environment": "prod"}]},
    "route": [{"to": {"team": "rates"}, "channels": ["sms"]}],
    "present": {"title": "Failed: {{workflowId}}", "fields": ["step"]},
}


@pytest.fixture
def clean(conn):
    conn.execute(
        "TRUNCATE schema_mappings, sources, unmapped_samples, suggestions, oncall_schedules, channel_preferences"
    )
    conn.execute("DELETE FROM team_members WHERE team_id NOT IN ('rates', 'pricing', 'payments', 'settlements')")
    conn.execute("DELETE FROM teams WHERE id NOT IN ('rates', 'pricing', 'payments', 'settlements')")
    conn.execute("DELETE FROM users WHERE id NOT IN ('admin', 'marek', 'mia', 'anna', 'piotr')")
    yield conn


def version(conn):
    return conn.execute("SELECT version FROM config_version").fetchone()["version"]


# --- rule tester (spec §4) -----------------------------------------------------------------------------


def test_rule_tester_with_a_pasted_event(client, login, clean):
    client.post("/api/mappings", json=WORKFLOW_MAPPING, headers=login("admin"))
    body = {"rule": RATES_RULE, "event": {"payload": ex.WORKFLOW_EVENT}}
    result = client.post("/api/rules/test", json=body, headers=login("mia")).json()
    assert_matches("RuleTestResult", result)
    [item] = result["letters"]
    assert (item["matched"], item["mappingName"], item["title"]) == (
        True,
        "workflow-event",
        "Failed: WF-20261023-00042",
    )
    assert item["fields"] == [{"path": "step", "value": "validate-counterparty"}]
    assert [c["result"] for c in item["conditions"]["children"]] == [True, True]
    assert item["recipients"] == [
        {"userId": "marek", "channels": ["sms"], "reason": "team: rates (channels from the rule)"},
        {"userId": "mia", "channels": ["sms"], "reason": "team: rates (channels from the rule)"},
    ]
    assert item["fingerprint"]

    staging = {"rule": RATES_RULE, "event": {"payload": {**ex.WORKFLOW_EVENT, "environment": "staging"}}}
    [miss] = client.post("/api/rules/test", json=staging, headers=login("mia")).json()["letters"]
    env = miss["conditions"]["children"][1]
    assert (miss["matched"], env["actual"], env["expected"], miss["recipients"]) == (False, "staging", "prod", [])


def test_rule_tester_on_an_existing_alert_and_bad_requests(client, login, conn):
    alert_id = insert_alert(conn)
    rule = {**RATES_RULE, "when": {"$service": "order-settlement"}}
    [item] = client.post("/api/rules/test", json={"rule": rule, "alertId": alert_id}, headers=login("mia")).json()[
        "letters"
    ]
    assert item["matched"] is True
    assert item["letter"]["$eventId"] == LETTER["$eventId"]
    both = {"rule": rule, "alertId": alert_id, "event": {"payload": {}}}
    assert client.post("/api/rules/test", json=both, headers=login("mia")).status_code == 422
    assert (
        client.post("/api/rules/test", json={"rule": rule, "alertId": 999999}, headers=login("mia")).status_code == 404
    )


# --- mappings and unmapped (spec §3, test 11) -----------------------------------------------------------


def test_mappings_crud_preview_and_config_version(client, login, clean):
    assert client.get("/api/mappings", headers=login("mia")).status_code == 403
    before = version(clean)
    created = client.post("/api/mappings", json=WORKFLOW_MAPPING, headers=login("admin"))
    assert created.status_code == 201
    assert_matches("Mapping", created.json())
    assert version(clean) == before + 1  # processors reload
    assert client.post("/api/mappings", json=WORKFLOW_MAPPING, headers=login("admin")).status_code == 409

    bad = {**WORKFLOW_MAPPING, "id": "bad", "fields": {"service": "x"}}
    assert client.post("/api/mappings", json=bad, headers=login("admin")).status_code == 422

    dotted = {"id": "k8s", "detect": {'labels["app.kubernetes.io/name"]': {"exists": True}},
              "fields": {"$service": 'labels["app.kubernetes.io/name"]'}}  # fmt: skip
    sample = {"payload": {"labels": {"app.kubernetes.io/name": "settler"}}}
    preview = client.post(
        "/api/mappings/preview", json={"mapping": dotted, "sample": sample}, headers=login("admin")
    ).json()
    assert (preview["errors"], preview["letters"][0]["$service"]) == ([], "settler")  # test 11: key with a dot
    miss = client.post(
        "/api/mappings/preview", json={"mapping": dotted, "sample": {"payload": {"x": 1}}}, headers=login("admin")
    ).json()
    assert miss["errors"][0]["path"] == "detect"

    assert client.delete("/api/mappings/workflow-event", headers=login("admin")).status_code == 204
    assert client.get("/api/mappings", headers=login("admin")).json() == []


def test_unmapped_groups(client, login, clean):
    clean.execute(
        "INSERT INTO unmapped_samples (shape_hash, source_id, sample, count) VALUES ('h1', 'http-default', %s, 3)",
        ('{"vendor": "acme", "check": {"name": "disk"}}',),
    )
    [group] = client.get("/api/unmapped", headers=login("admin")).json()
    assert_matches("UnmappedGroup", group)
    assert (group["count"], group["keys"]) == (3, ["check", "check.name", "vendor"])


# --- sources, users, teams (admin; test 20 onboarding) -----------------------------------------------


def test_source_token_is_shown_once(client, login, clean):
    created = client.post("/api/sources", json={"id": "new-src", "type": "http"}, headers=login("admin")).json()
    assert len(created["token"]) > 20
    stored = clean.execute("SELECT token_sha256 FROM sources WHERE id = 'new-src'").fetchone()["token_sha256"]
    assert stored and created["token"] not in stored
    [listed] = client.get("/api/sources", headers=login("admin")).json()
    assert "token" not in listed
    assert client.post("/api/sources", json={"id": "x", "type": "http"}, headers=login("mia")).status_code == 403


def test_onboard_a_team_in_one_call(client, login, clean):
    """Spec §13 test 20: admin creates the team and its people with one API call."""
    body = {
        "id": "fx", "name": "FX",
        "newUsers": [{"id": "fiona", "name": "Fiona", "phone": "+48500000099", "timezone": "Europe/Warsaw"}],
        "members": [{"userId": "fiona", "role": "manager"}, {"userId": "mia", "role": "member"}],
    }  # fmt: skip
    assert client.post("/api/teams", json=body, headers=login("marek")).status_code == 403
    team = client.post("/api/teams", json=body, headers=login("admin")).json()
    assert_matches("Team", team)
    assert team["members"] == [{"userId": "fiona", "role": "manager"}, {"userId": "mia", "role": "member"}]
    unknown = {**body, "id": "fx2", "newUsers": [], "members": [{"userId": "ghost", "role": "member"}]}
    assert client.post("/api/teams", json=unknown, headers=login("admin")).status_code == 422
    assert all(t["id"] != "fx2" for t in client.get("/api/teams", headers=login("mia")).json())  # rolled back


# --- preferences and on-call -------------------------------------------------------------------------


def test_my_preferences_with_working_hours(client, login, clean):
    prefs = {"timezone": "Europe/London", "workingHours": {"between": "09:00-17:00", "days": [1, 2, 3, 4, 5]},
             "bySeverity": {"critical": ["sms", "email"], "warning": ["email"]}, "outsideWorkingHours": ["phone"]}  # fmt: skip
    saved = client.put("/api/preferences/me", json=prefs, headers=login("anna")).json()
    assert saved == prefs
    stored = clean.execute("SELECT by_severity FROM channel_preferences WHERE owner_id = 'anna'").fetchone()[
        "by_severity"
    ]
    assert stored["critical"] == {"schedule": [{"between": "09:00-17:00", "days": [1, 2, 3, 4, 5], "use": ["sms", "email"]}],
                                  "otherwise": ["phone"]}  # B's routing format  # fmt: skip
    assert client.put("/api/preferences/me", json={"timezone": "Mars/Base"}, headers=login("anna")).status_code == 422


def test_oncall_rotation(client, login, clean):
    body = {"rotation": ["marek", "mia"], "shiftLength": "1d", "handoffTime": "09:00", "timezone": "Europe/Warsaw",
            "startsOn": "2026-11-02"}  # fmt: skip
    assert client.put("/api/oncall/rates", json=body, headers=login("mia")).status_code == 403
    saved = client.put("/api/oncall/rates", json=body, headers=login("marek")).json()
    assert_matches("OncallSchedule", saved)
    assert saved["currentUserId"] in ("marek", "mia") and saved["currentUntil"]
    outsider = {**body, "rotation": ["anna"]}
    assert client.put("/api/oncall/rates", json=outsider, headers=login("marek")).status_code == 422
    assert [s["teamId"] for s in client.get("/api/oncall", headers=login("anna")).json()] == ["rates"]


# --- suggestions ("propose fixes") ---------------------------------------------------------------------


def test_propose_a_fix_and_decide(client, login, clean):
    client.post("/api/rules", json=RATES_RULE, headers=login("marek"))
    alert_id = insert_alert(clean, rule_id="rates-workflows", title="s1")
    created = client.post(f"/api/alerts/{alert_id}/suggestions", json={"target": "rule", "text": "only prod"},
                          headers=login("anna"))  # fmt: skip
    assert created.status_code == 201
    suggestion = created.json()
    assert_matches("Suggestion", suggestion)
    assert (suggestion["targetId"], suggestion["author"], suggestion["status"]) == ("rates-workflows", "anna", "open")
    assert [s["id"] for s in client.get("/api/suggestions?team=rates", headers=login("marek")).json()] == [
        suggestion["id"]
    ]
    assert client.post(f"/api/suggestions/{suggestion['id']}/accept", headers=login("mia")).status_code == 403
    decided = client.post(f"/api/suggestions/{suggestion['id']}/accept", headers=login("marek")).json()
    assert (decided["status"], decided["decidedBy"]) == ("accepted", "marek")
    assert client.post(f"/api/suggestions/{suggestion['id']}/reject", headers=login("marek")).status_code == 409
