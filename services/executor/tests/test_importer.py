"""Runbook import (spec §7 table): deterministic, no LLM; output valid against contracts/runbook.schema.json."""

import pytest

from services.executor.importer import ImportError_, import_runbook
from services.executor.runbooks import validate

SPEC_YAML = """
runbook: settlement-timeout
match: { all: [ { type: WORKFLOW_FAILED }, { error.code: TIMEOUT } ] }
steps:
  - check: "Is counterparty-service healthy?"
    action: http_get
    url: http://counterparty-service/health
  - action: retry_workflow
    params: { workflowId: "{{workflowId}}" }
    if: fails twice
    then: escalate
  - action: escalate
    team: counterparty-support
    approval: required
"""

# spec §7 "Runbook YAML z treści zadania po imporcie"
SPEC_CANONICAL_STEPS = [
    {"id": "check-counterparty", "kind": "action", "title": "Is counterparty-service healthy?", "action": "http_get",
     "params": {"url": "http://counterparty-service/health"}, "approval": "none"},
    {"id": "retry", "kind": "action", "action": "retry_workflow", "params": {"workflowId": "{{workflowId}}"},
     "approval": "none", "attempts": 2, "resolves": True, "on_failure": "escalate"},
    {"id": "escalate", "kind": "action", "action": "escalate", "params": {"team": "counterparty-support"},
     "approval": "required"},
]  # fmt: skip


def test_spec_yaml_example_gives_the_spec_canonical_form():
    """Spec §13 test 12: the YAML runbook from the task, imported."""
    canonical = import_runbook("settlement-timeout", "settlements", "yaml", SPEC_YAML)
    assert canonical["steps"] == SPEC_CANONICAL_STEPS
    assert canonical["match"] == {"all": [{"type": "WORKFLOW_FAILED"}, {"error.code": "TIMEOUT"}]}
    assert (canonical["format"], canonical["mode"], canonical["owner"]) == ("yaml", "manual", {"team": "settlements"})
    assert validate(canonical) == []


@pytest.mark.parametrize(("phrase", "attempts"), [("fails twice", 2), ("fails 3 times", 3), ("fail once", 1)])
def test_if_phrases(phrase, attempts):
    original = f"steps:\n  - action: retry_workflow\n    if: {phrase}\n    then: escalate\n  - action: escalate\n"
    retry = import_runbook("rb", "t", "yaml", original)["steps"][0]
    assert (retry["attempts"], retry["on_failure"]) == (attempts, "escalate")


def test_unknown_field_or_phrase_becomes_a_step_to_review():
    original = """
steps:
  - action: http_get
    url: http://x/health
    retries_with_jitter: true
  - action: retry_workflow
    if: looks flaky
  - Call the counterparty desk
"""
    steps = import_runbook("rb", "t", "yaml", original)["steps"]
    assert [(s["kind"], s.get("needs_review", False)) for s in steps] == [
        ("text", True),
        ("text", True),
        ("text", False),
    ]
    assert "retries_with_jitter" in steps[0]["title"] and "looks flaky" in steps[1]["title"]
    assert steps[2]["text"] == "Call the counterparty desk"
    assert validate(import_runbook("rb", "t", "yaml", original)) == []


def test_then_to_an_unknown_step_is_flagged():
    original = "steps:\n  - action: retry_workflow\n    if: fails twice\n    then: nowhere\n"
    step = import_runbook("rb", "t", "yaml", original)["steps"][0]
    assert step["needs_review"] is True and "on_failure" not in step


def test_wait_and_text_steps_and_duplicate_ids():
    original = "steps:\n  - action: notify\n  - wait: 2m\n  - text: Check the dashboard\n  - action: notify\n"
    steps = import_runbook("rb", "t", "yaml", original)["steps"]
    assert [s["id"] for s in steps] == ["notify", "wait", "step", "notify-2"]
    assert steps[1] == {"id": "wait", "kind": "wait", "duration": "2m"}


def test_markdown_numbered_list():
    """Spec §13 test 4: the alert shows a Markdown runbook."""
    original = "Payments errors:\n1. Open the dashboard\n   and find the endpoint\n2) Roll back the deploy\n\nnotes"
    steps = import_runbook("rb", "t", "markdown", original)["steps"]
    assert [(s["id"], s["kind"], s["text"]) for s in steps] == [
        ("step-1", "text", "Open the dashboard and find the endpoint"),
        ("step-2", "text", "Roll back the deploy"),
    ]


def test_checklist():
    """Spec §13 test 13: checklist items become manual steps with a checkbox."""
    steps = import_runbook("rb", "t", "checklist", "- [ ] Check latency\n- [x] Compare engines\nnot an item")["steps"]
    assert steps == [
        {"id": "item-1", "kind": "text", "checkbox": True, "text": "Check latency"},
        {"id": "item-2", "kind": "text", "checkbox": True, "text": "Compare engines"},
    ]


def test_free_text_and_link():
    assert import_runbook("rb", "t", "text", "  Call Bob.\nThen Alice. ")["steps"] == [
        {"id": "step-1", "kind": "text", "text": "Call Bob.\nThen Alice."}
    ]
    link = import_runbook("rb", "t", "link", " https://wiki.example.test/runbooks/payments ")
    assert (link["link"], link["steps"]) == ("https://wiki.example.test/runbooks/payments", [])
    assert validate(link) == []


def test_invalid_yaml():
    with pytest.raises(ImportError_):
        import_runbook("rb", "t", "yaml", "steps: [unclosed")
    with pytest.raises(ImportError_):
        import_runbook("rb", "t", "yaml", "just a string")
