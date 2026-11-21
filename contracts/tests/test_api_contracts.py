"""Contract checks for owner C: openapi.yaml, WebSocket messages, NOTIFY payloads."""

import json
import re
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from contracts.notify import (
    MAX_IDS_PER_MESSAGE,
    alerts_changed_payloads,
    config_changed_payload,
    parse_alerts_changed,
)

CONTRACTS = Path(__file__).resolve().parents[1]
BASE = "https://alerting.local/contracts/"
HTTP_METHODS = {"get", "put", "post", "patch", "delete"}


def load_json(name: str) -> dict:
    return json.loads((CONTRACTS / name).read_text(encoding="utf-8"))


OPENAPI = yaml.safe_load((CONTRACTS / "openapi.yaml").read_text(encoding="utf-8"))
WS = load_json("ws-messages.schema.json")
NOTIFY = load_json("notify.schema.json")

SCHEMA_FILES = ("route.schema.json", "runbook.schema.json", "ws-messages.schema.json", "notify.schema.json")
REGISTRY = Registry().with_resources(
    [
        (BASE + "openapi.yaml", Resource.from_contents(OPENAPI, default_specification=DRAFT202012)),
        *((BASE + name, Resource.from_contents(load_json(name))) for name in SCHEMA_FILES),
    ]
)


def validator(ref: str) -> Draft202012Validator:
    return Draft202012Validator({"$ref": ref}, registry=REGISTRY)


def api_schema(name: str) -> Draft202012Validator:
    return validator(f"{BASE}openapi.yaml#/components/schemas/{name}")


# --- openapi.yaml structure ----------------------------------------------------------------------------


def walk(node, path=""):
    if isinstance(node, dict):
        for key, value in node.items():
            yield path, key, value
            yield from walk(value, f"{path}/{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from walk(value, f"{path}/{i}")


def operations():
    for path, item in OPENAPI["paths"].items():
        for method, op in item.items():
            if method in HTTP_METHODS:
                yield path, method, item, op


def test_openapi_version_and_server():
    assert OPENAPI["openapi"].startswith("3.1")
    assert OPENAPI["servers"] == [{"url": "/api"}]


def test_every_ref_resolves():
    resolver = REGISTRY.resolver(base_uri=BASE + "openapi.yaml")
    refs = [value for _, key, value in walk(OPENAPI) if key == "$ref"]
    assert refs
    for ref in refs:
        resolver.lookup(ref)  # raises when unresolvable


def test_component_schemas_are_valid_json_schema():
    for name, schema in OPENAPI["components"]["schemas"].items():
        try:
            Draft202012Validator.check_schema(schema)
        except Exception as exc:  # pragma: no cover - message only
            raise AssertionError(f"components.schemas.{name}: {exc}") from exc


def test_operation_ids_unique_and_present():
    ids = [op.get("operationId") for _, _, _, op in operations()]
    assert all(ids), "every operation needs an operationId"
    assert len(ids) == len(set(ids))


def test_path_parameters_are_declared():
    resolver = REGISTRY.resolver(base_uri=BASE + "openapi.yaml")

    def names(params):
        for p in params or []:
            if "$ref" in p:
                p = resolver.lookup(p["$ref"]).contents
            if p["in"] == "path":
                yield p["name"]

    for path, _, item, op in operations():
        expected = set(re.findall(r"{(\w+)}", path))
        declared = set(names(item.get("parameters"))) | set(names(op.get("parameters")))
        assert expected == declared, f"{path}: {expected} != {declared}"


def test_every_operation_has_a_success_response():
    for path, method, _, op in operations():
        assert any(code.startswith("2") for code in op["responses"]), f"{method} {path}"


PUBLIC = {
    ("get", "/auth/users"),
    ("post", "/auth/login"),
    ("get", "/approvals/by-token/{token}"),
    ("post", "/approvals/by-token/{token}"),
    ("post", "/inbound/sms"),
}


def test_only_expected_endpoints_are_public():
    public = {(m, p) for p, m, _, op in operations() if op.get("security") == []}
    assert public == PUBLIC


def test_required_paths_from_the_plan_exist():
    # spec §9 screens -> endpoints (docs/plans/c-api-web.md)
    for path in [
        "/auth/login",
        "/me",
        "/alerts",
        "/alerts/{alertId}",
        "/alerts/{alertId}/timeline",
        "/alerts/{alertId}/ack",
        "/alerts/{alertId}/resolve",
        "/rules",
        "/rules/{ruleId}",
        "/rules/test",
        "/runbooks/import",
        "/runbooks/{runbookId}/approve",
        "/runbook-runs/{runId}/steps/{stepId}/done",
        "/approvals/{approvalId}/approve",
        "/approvals/by-token/{token}",
        "/mappings/preview",
        "/unmapped",
        "/preferences/me",
        "/oncall",
        "/alerts/{alertId}/suggestions",
        "/teams",
        "/sources",
        "/inbound/sms",
    ]:
        assert path in OPENAPI["paths"], path


# --- shapes --------------------------------------------------------------------------------------------

ALERT = {
    "id": 42,
    "ruleId": "settlements-prod-failures",
    "owner": {"team": "settlements"},
    "teams": ["settlements"],
    "state": "open",
    "severity": "critical",
    "title": "Settlement failed: wf-123",
    "color": "red",
    "count": 1,
    "firstSeen": "2026-11-21T09:14:02Z",
    "lastSeen": "2026-11-21T09:14:02Z",
    "hasDeadNotification": False,
    "version": "2026-11-21T09:14:02.000000Z|open|1",
}


def test_alert_summary_matches_in_openapi_and_ws():
    api_schema("AlertSummary").validate(ALERT)
    validator(BASE + "ws-messages.schema.json#/$defs/alertSummary").validate(ALERT)


def test_alert_summary_keys_are_the_same_in_openapi_and_ws():
    api = OPENAPI["components"]["schemas"]["AlertSummary"]
    ws = WS["$defs"]["alertSummary"]
    assert set(api["required"]) == set(ws["required"])
    assert set(api["properties"]) == set(ws["properties"])


def test_rule_input_from_spec_validates():
    rule = {
        "id": "anna-payments-critical",
        "owner": {"user": "anna"},
        "when": {"all": [{"$service": "payments-api"}, {"$severity": "critical"}]},
        "route": [
            {
                "to": {"user": "anna"},
                "channels": {
                    "schedule": [{"between": "08:00-20:00", "use": ["sms"]}],
                    "otherwise": ["email"],
                },
            }
        ],
        "runbooks": ["high-error-rate-payments"],
    }
    api_schema("RuleInput").validate(rule)
    rule["route"] = [{"to": {"user": "anna"}, "channels": ["pigeon"]}]
    assert list(api_schema("RuleInput").iter_errors(rule)), "route is validated with route.schema.json"


def test_saved_rule_has_metadata_and_input_rejects_unknown_fields():
    rule = {"id": "r", "owner": {"team": "rates"}, "when": {"payload.desk": "rates"}}
    api_schema("RuleInput").validate(rule)
    api_schema("Rule").validate({**rule, "updatedBy": "anna", "updatedAt": "2026-11-21T10:00:00Z"})
    assert list(api_schema("RuleInput").iter_errors({**rule, "colour": "red"}))
    saved = {**rule, "updatedBy": "anna", "updatedAt": "2026-11-21T10:00:00Z"}
    assert list(api_schema("Rule").iter_errors({**saved, "colour": "red"}))


def test_saved_suggestion_validates():
    data = {"target": "rule", "targetId": "r", "text": "raise the threshold"}
    api_schema("SuggestionInput").validate(data)
    api_schema("Suggestion").validate(
        {**data, "id": 1, "alertId": 42, "author": "anna", "status": "open", "createdAt": "2026-11-21T10:00Z"}
    )
    assert list(api_schema("SuggestionInput").iter_errors({**data, "id": 1}))


def test_team_input_onboarding_in_one_call():
    api_schema("TeamInput").validate(
        {
            "id": "fx-options",
            "name": "FX Options",
            "newUsers": [{"id": "ola", "name": "Ola", "phone": "+48500100200", "timezone": "Europe/Warsaw"}],
            "members": [{"userId": "ola", "role": "manager"}],
        }
    )


@pytest.mark.parametrize(
    "error",
    [
        {"error": {"code": "forbidden", "message": "manager of rates required"}},
        {"error": {"code": "invalid", "message": "bad rule", "details": [{"path": "when", "message": "x"}]}},
    ],
)
def test_error_shape(error):
    api_schema("Error").validate(error)


# --- WebSocket messages --------------------------------------------------------------------------------

CLIENT = validator(BASE + "ws-messages.schema.json#/$defs/clientMessage")
SERVER = validator(BASE + "ws-messages.schema.json#/$defs/serverMessage")


@pytest.mark.parametrize(
    "msg",
    [
        {"type": "subscribe"},
        {"type": "subscribe", "filters": {"team": "rates", "severity": ["critical"], "state": ["open"]}},
        {"type": "pong"},
    ],
)
def test_valid_client_messages(msg):
    CLIENT.validate(msg)


@pytest.mark.parametrize(
    "msg",
    [
        {"type": "subscribe", "filters": {"severity": ["fatal"]}},
        {"type": "subscribe", "filters": {"colour": "red"}},
        {"type": "hello"},
    ],
)
def test_invalid_client_messages(msg):
    assert list(CLIENT.iter_errors(msg))


@pytest.mark.parametrize(
    "msg",
    [
        {"type": "alert.upsert", "alert": ALERT},
        {"type": "ping"},
        {"type": "resync", "reason": "listener_reconnected"},
    ],
)
def test_valid_server_messages(msg):
    SERVER.validate(msg)


def test_upsert_without_alert_is_invalid():
    assert list(SERVER.iter_errors({"type": "alert.upsert"}))


# --- NOTIFY --------------------------------------------------------------------------------------------

ALERTS_CHANGED = validator(BASE + "notify.schema.json#/$defs/alerts_changed")


def test_alerts_changed_split_dedup_and_size():
    payloads = alerts_changed_payloads([3, 1, 3, *range(10, 1210)])
    assert len(payloads) == 4
    ids = [i for p in payloads for i in json.loads(p)["ids"]]
    assert ids == sorted(set(ids)) and len(ids) == 1202
    for p in payloads:
        ALERTS_CHANGED.validate(json.loads(p))
        assert len(json.loads(p)["ids"]) <= MAX_IDS_PER_MESSAGE
        assert len(p.encode()) < 8000


def test_alerts_changed_with_max_bigint_ids_fits_the_limit():
    (payload,) = alerts_changed_payloads(range(2**63 - MAX_IDS_PER_MESSAGE, 2**63))
    assert len(payload.encode()) < 8000


def test_no_ids_no_message():
    assert alerts_changed_payloads([]) == []


@pytest.mark.parametrize("payload", ["", "nope", "[]", '{"ids": "x"}', '{"other": 1}'])
def test_parse_never_raises(payload):
    assert parse_alerts_changed(payload) == []


def test_parse_drops_non_ints():
    assert parse_alerts_changed('{"ids": [1, "2", true, 3]}') == [1, 3]


def test_config_changed():
    schema = validator(BASE + "notify.schema.json#/$defs/config_changed")
    schema.validate(json.loads(config_changed_payload(7)))
