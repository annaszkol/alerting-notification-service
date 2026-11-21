"""Rule CRUD: validation, permissions, config_version + NOTIFY config_changed (spec §4, §9)."""

import copy

import pytest

from services.api.tests.helpers import assert_matches

RATES_RULE = {
    "id": "rates-large-rejections",
    "owner": {"team": "rates"},
    "when": {
        "all": [
            {"$schema": {"glob": "trade.rejected.*"}},
            {"payload.desk": "rates"},
            {"payload.notional": {"gt": 10000000}},
        ]
    },
    "route": [{"to": {"team": "rates"}, "channels": ["email"]}],
    "present": {"title": "Rejected {{payload.currency}} {{payload.notional}} — {{payload.reason}}"},
    "dedup": {"key": ["payload.tradeId"], "mode": "until_resolved"},
}

PERSONAL_RULE = {
    "id": "anna-payments-critical",
    "owner": {"user": "anna"},
    "when": {"all": [{"$service": "payments-api"}, {"$severity": "critical"}]},
    "route": [
        {
            "to": {"user": "anna"},
            "channels": {"schedule": [{"between": "08:00-20:00", "use": ["sms"]}], "otherwise": ["email"]},
        }
    ],
}


def config_version(conn) -> int:
    return conn.execute("SELECT version FROM config_version").fetchone()["version"]


def test_create_rule_saves_document_and_bumps_config(client, login, conn, listener):
    received = listener("config_changed")
    response = client.post("/api/rules", json=RATES_RULE, headers=login("marek"))
    assert response.status_code == 201, response.text
    rule = response.json()
    assert_matches("Rule", rule)
    assert rule["updatedBy"] == "marek"
    assert rule["enabled"] is True
    assert rule["when"] == RATES_RULE["when"]
    assert config_version(conn) == 2
    assert received() == [{"version": 2}]

    listed = client.get("/api/rules", params={"team": "rates"}, headers=login("mia")).json()
    assert [r["id"] for r in listed] == ["rates-large-rejections"]


def test_auto_resolve_after_roundtrip(client, login):
    rule = {
        **RATES_RULE,
        "autoResolveAfter": "30m",
        "dedup": {"key": ["x"], "mode": "window", "window": "5m"},
    }
    saved = client.post("/api/rules", json=rule, headers=login("marek")).json()
    assert saved["autoResolveAfter"] == "30m"
    assert saved["dedup"]["window"] == "5m"


# --- permissions ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "user, rule, status",
    [
        ("marek", RATES_RULE, 201),  # manager of the owner team
        ("mia", RATES_RULE, 403),  # member only
        ("piotr", RATES_RULE, 403),  # manager of another team
        ("admin", RATES_RULE, 201),
        ("anna", PERSONAL_RULE, 201),  # own personal rule
        ("mia", PERSONAL_RULE, 403),  # someone else's personal rule
        ("admin", PERSONAL_RULE, 201),
    ],
)
def test_who_can_create(client, login, conn, user, rule, status):
    response = client.post("/api/rules", json=rule, headers=login(user))
    assert response.status_code == status, response.text
    if status == 403:
        assert response.json()["error"]["code"] == "forbidden"
        assert config_version(conn) == 1


def test_member_cannot_edit_disable_or_delete_team_rule(client, login):
    client.post("/api/rules", json=RATES_RULE, headers=login("marek"))
    headers = login("mia")
    changed = {**RATES_RULE, "present": {"title": "x"}}
    assert client.put(f"/api/rules/{RATES_RULE['id']}", json=changed, headers=headers).status_code == 403
    assert client.patch(f"/api/rules/{RATES_RULE['id']}", json={"enabled": False}, headers=headers).status_code == 403
    assert client.delete(f"/api/rules/{RATES_RULE['id']}", headers=headers).status_code == 403


def test_moving_a_rule_needs_rights_on_both_owners(client, login):
    client.post("/api/rules", json=RATES_RULE, headers=login("marek"))
    moved = {**RATES_RULE, "owner": {"team": "pricing"}, "route": []}
    # marek manages rates but not pricing, piotr manages pricing but not rates
    assert client.put(f"/api/rules/{RATES_RULE['id']}", json=moved, headers=login("marek")).status_code == 403
    assert client.put(f"/api/rules/{RATES_RULE['id']}", json=moved, headers=login("piotr")).status_code == 403
    assert client.put(f"/api/rules/{RATES_RULE['id']}", json=moved, headers=login("admin")).status_code == 200


def test_reading_rules_needs_login(client):
    assert client.get("/api/rules").status_code == 401


# --- validation ----------------------------------------------------------------------------------------


def broken(**changes):
    rule = copy.deepcopy(RATES_RULE)
    for path, value in changes.items():
        node = rule
        *parents, last = path.split("__")
        for p in parents:
            node = node[int(p)] if p.isdigit() else node[p]
        node[int(last) if last.isdigit() else last] = value
    return rule


@pytest.mark.parametrize(
    "rule, error_path",
    [
        (broken(when={"all": [{"payload.notional": {"bigger": 1}}]}), "when.all.0.payload.notional.bigger"),
        (broken(when={"any": []}), "when.any"),
        (broken(when={"all": [{"error.message": {"regex": "("}}]}), "when.all.0.error.message.regex"),
        (broken(when={"all": [{"error.code": {"in": "TIMEOUT"}}]}), "when.all.0.error.code.in"),
        (broken(route=[{"to": {"team": "rates"}, "channels": ["pigeon"]}]), "route.0.channels"),
        (broken(route=[{"to": {"team": "nope"}, "channels": ["email"]}]), "route.0.to"),
        (broken(owner={"team": "nope"}), "owner"),
        (broken(present={"title": "{{payload.currency"}), "present.title"),
        (broken(dedup={"mode": "window"}), "dedup.window"),
        (broken(autoResolveAfter="30 minutes"), "autoResolveAfter"),
        (broken(id="Not A Slug"), "id"),
        ({**RATES_RULE, "colour": "red"}, "body"),
    ],
)
def test_invalid_rules_are_rejected_with_paths(client, login, conn, rule, error_path):
    response = client.post("/api/rules", json=rule, headers=login("admin"))
    assert response.status_code == 422, response.text
    body = response.json()
    assert_matches("Error", body)
    paths = [d["path"] for d in body["error"]["details"]]
    assert any(p == error_path or p.startswith(error_path + ".") for p in paths), paths
    assert config_version(conn) == 1


def test_dotted_keys_in_brackets_are_valid_paths(client, login):
    rule = broken(when={"all": [{'labels["app.kubernetes.io/name"]': "settlement"}]})
    assert client.post("/api/rules", json=rule, headers=login("admin")).status_code == 201


def test_duplicate_id_is_409(client, login, conn):
    assert client.post("/api/rules", json=RATES_RULE, headers=login("marek")).status_code == 201
    response = client.post("/api/rules", json=RATES_RULE, headers=login("marek"))
    assert response.status_code == 409
    assert config_version(conn) == 2


# --- update, disable, delete ---------------------------------------------------------------------------


def test_replace_updates_document_and_bumps(client, login, conn, listener):
    client.post("/api/rules", json=RATES_RULE, headers=login("marek"))
    received = listener("config_changed")
    changed = {**RATES_RULE, "present": {"title": "Rejected {{payload.tradeId}}"}}
    response = client.put(f"/api/rules/{RATES_RULE['id']}", json=changed, headers=login("admin"))
    assert response.status_code == 200
    assert response.json()["present"] == {"title": "Rejected {{payload.tradeId}}"}
    assert response.json()["updatedBy"] == "admin"
    assert received() == [{"version": 3}]


def test_replace_cannot_change_id(client, login):
    client.post("/api/rules", json=RATES_RULE, headers=login("marek"))
    response = client.put(f"/api/rules/{RATES_RULE['id']}", json={**RATES_RULE, "id": "other"}, headers=login("marek"))
    assert response.status_code == 422


def test_disable_and_delete(client, login, conn):
    headers = login("marek")
    client.post("/api/rules", json=RATES_RULE, headers=headers)
    response = client.patch(f"/api/rules/{RATES_RULE['id']}", json={"enabled": False}, headers=headers)
    assert response.status_code == 200 and response.json()["enabled"] is False
    assert client.patch(f"/api/rules/{RATES_RULE['id']}", json={"enabled": "no"}, headers=headers).status_code == 422
    assert client.delete(f"/api/rules/{RATES_RULE['id']}", headers=headers).status_code == 204
    assert client.get(f"/api/rules/{RATES_RULE['id']}", headers=headers).status_code == 404
    assert config_version(conn) == 4  # create, disable, delete


def test_unknown_rule_is_404(client, login):
    headers = login("admin")
    assert client.get("/api/rules/nope", headers=headers).status_code == 404
    assert client.put("/api/rules/nope", json={**RATES_RULE, "id": "nope"}, headers=headers).status_code == 404
    assert client.delete("/api/rules/nope", headers=headers).status_code == 404


def test_failed_write_rolls_back_and_sends_nothing(client, login, conn, listener, monkeypatch):
    """The insert, the version bump and NOTIFY are one transaction."""
    received = listener("config_changed")
    from services.api.domain import rules as domain

    def boom(*_):
        raise RuntimeError("crash after bump")

    original = domain.bump_config_version

    def bump_then_crash(c):
        original(c)
        boom()

    monkeypatch.setattr(domain, "bump_config_version", bump_then_crash)
    with pytest.raises(RuntimeError):
        client.post("/api/rules", json=RATES_RULE, headers=login("marek"))
    assert conn.execute("SELECT count(*) AS n FROM rules").fetchone()["n"] == 0
    assert config_version(conn) == 1
    assert received(timeout=0.5) == []
