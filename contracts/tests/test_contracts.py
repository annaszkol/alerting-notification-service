"""Contract checks: the examples from docs/specs.md must validate, broken documents must not."""

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from contracts.actions import Approval, effective_approval

CONTRACTS = Path(__file__).resolve().parents[1]


def validator(name: str) -> Draft202012Validator:
    schema = json.loads((CONTRACTS / name).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


ROUTE = validator("route.schema.json")
RUNBOOK = validator("runbook.schema.json")


# --- route ---------------------------------------------------------------------------------------------

SPEC_ROUTES = {
    "settlements-prod-failures": [{"to": {"oncall": "settlements"}, "channels": ["phone", "sms"]}],
    "pricing-warnings (UI only)": [],
    "rates-large-rejections": [{"to": {"team": "rates"}, "channels": ["email"]}],
    "anna-payments-critical": [
        {
            "to": {"user": "anna"},
            "channels": {"schedule": [{"between": "08:00-20:00", "use": ["sms"]}], "otherwise": ["email"]},
        }
    ],
    "team role + preferences": [
        {"to": {"team": "settlements", "role": "manager"}, "channels": "preferences"},
    ],
    "channels omitted = preferences": [{"to": {"user": "anna"}}],
    "schedule over midnight, weekdays": [
        {
            "to": {"user": "anna"},
            "channels": {
                "schedule": [{"between": "22:00-06:00", "days": [1, 2, 3, 4, 5], "use": ["phone"]}],
                "otherwise": [],
            },
        }
    ],
}


@pytest.mark.parametrize("route", SPEC_ROUTES.values(), ids=SPEC_ROUTES.keys())
def test_spec_routes_are_valid(route):
    ROUTE.validate(route)


BAD_ROUTES = {
    "unknown channel": [{"to": {"user": "anna"}, "channels": ["pigeon"]}],
    "two recipient kinds": [{"to": {"user": "anna", "team": "rates"}, "channels": ["sms"]}],
    "role without team": [{"to": {"oncall": "rates", "role": "manager"}}],
    "empty channel list": [{"to": {"user": "anna"}, "channels": []}],
    "bad time window": [
        {
            "to": {"user": "anna"},
            "channels": {"schedule": [{"between": "8-20", "use": ["sms"]}], "otherwise": []},
        }
    ],
    "schedule without otherwise": [
        {"to": {"user": "anna"}, "channels": {"schedule": [{"between": "08:00-20:00", "use": ["sms"]}]}}
    ],
    "missing to": [{"channels": ["sms"]}],
}


@pytest.mark.parametrize("route", BAD_ROUTES.values(), ids=BAD_ROUTES.keys())
def test_bad_routes_are_rejected(route):
    assert list(ROUTE.iter_errors(route))


# --- runbook -------------------------------------------------------------------------------------------

SPEC_RUNBOOK = {
    "id": "settlement-timeout",
    "format": "yaml",
    "match": {"all": [{"type": "WORKFLOW_FAILED"}, {"error.code": "TIMEOUT"}]},
    "steps": [
        {
            "id": "check-counterparty",
            "kind": "action",
            "title": "Is counterparty-service healthy?",
            "action": "http_get",
            "params": {"url": "http://counterparty-service/health"},
            "approval": "none",
        },
        {
            "id": "retry",
            "kind": "action",
            "action": "retry_workflow",
            "params": {"workflowId": "{{workflowId}}"},
            "approval": "none",
            "attempts": 2,
            "resolves": True,
            "on_failure": "escalate",
        },
        {
            "id": "escalate",
            "kind": "action",
            "action": "escalate",
            "params": {"team": "counterparty-support"},
            "approval": "required",
        },
    ],
}

CHECKLIST_RUNBOOK = {
    "id": "pricing-latency",
    "format": "checklist",
    "owner": {"team": "pricing"},
    "mode": "manual",
    "steps": [
        {"id": "s1", "kind": "text", "text": "Check dashboards for {{$service}}", "checkbox": True},
        {"id": "s2", "kind": "wait", "duration": "2m"},
        {"id": "s3", "kind": "action", "action": "notify", "only_if_open": True, "needs_review": True,
         "params": {"to": {"oncall": "pricing"}, "message": "latency still high"}},
    ],
}  # fmt: skip


@pytest.mark.parametrize("runbook", [SPEC_RUNBOOK, CHECKLIST_RUNBOOK], ids=["spec-yaml", "checklist"])
def test_valid_runbooks(runbook):
    RUNBOOK.validate(runbook)


def with_step(step: dict) -> dict:
    return {"id": "rb", "steps": [step]}


BAD_RUNBOOKS = {
    "action step without action": with_step({"id": "a", "kind": "action"}),
    "text step without text": with_step({"id": "a", "kind": "text"}),
    "wait without duration": with_step({"id": "a", "kind": "wait"}),
    "bad duration": with_step({"id": "a", "kind": "wait", "duration": "2 minutes"}),
    "unknown kind": with_step({"id": "a", "kind": "shell", "text": "rm -rf /"}),
    "unknown approval": with_step({"id": "a", "kind": "text", "text": "x", "approval": "maybe"}),
    "action field on text step": with_step({"id": "a", "kind": "text", "text": "x", "action": "http_get"}),
    "unknown step field": with_step({"id": "a", "kind": "text", "text": "x", "if": "fails twice"}),
    "zero attempts": with_step({"id": "a", "kind": "action", "action": "http_get", "attempts": 0}),
    "bad mode": {"id": "rb", "mode": "yolo", "steps": []},
}


@pytest.mark.parametrize("runbook", BAD_RUNBOOKS.values(), ids=BAD_RUNBOOKS.keys())
def test_bad_runbooks_are_rejected(runbook):
    assert list(RUNBOOK.iter_errors(runbook))


def test_runbook_example_in_markdown_is_valid():
    text = (CONTRACTS / "runbooks.md").read_text(encoding="utf-8")
    example = text.split("```json", 1)[1].split("```", 1)[0]
    RUNBOOK.validate(json.loads(example))


# --- actions -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("step", "action_min", "expected"),
    [
        (Approval.NONE, Approval.NONE, Approval.NONE),
        (Approval.REQUIRED, Approval.NONE, Approval.REQUIRED),  # runbook raises the level
        (Approval.NONE, Approval.REQUIRED, Approval.REQUIRED),  # runbook cannot lower it
        (Approval.REQUIRED, Approval.REQUIRED, Approval.REQUIRED),
    ],
)
def test_effective_approval(step, action_min, expected):
    assert effective_approval(step, action_min) is expected


def test_interfaces_import():
    from contracts import delivery

    assert {c.value for c in delivery.Channel} == {"ui", "email", "sms", "phone"}
    assert [s.value for s in delivery.Severity] == ["info", "warning", "error", "critical"]
