"""Sanity checks for the Letter contract (owner A, spec §3, §5)."""

from __future__ import annotations

from contracts.letter import Letter


def _workflow_letter() -> Letter:
    return Letter(
        fields={
            "type": "WORKFLOW_FAILED",
            "workflowName": "order-settlement",
            "severity": "ERROR",
            "environment": "prod",
            "error_code": "TIMEOUT",
        },
        raw={
            "type": "WORKFLOW_FAILED",
            "workflowName": "order-settlement",
            "severity": "ERROR",
            "environment": "prod",
            "error": {"code": "TIMEOUT"},
        },
        source_id="workflows.kafka",
        event_id="01H8X",
        received_at="2026-10-23T09:14:02.400Z",
        schema="workflow.failed.v1",
    )


def test_view_merges_fields_payload_and_reserved() -> None:
    view = _workflow_letter().view()
    assert view["type"] == "WORKFLOW_FAILED"
    assert view["workflowName"] == "order-settlement"
    assert view["payload"]["error"]["code"] == "TIMEOUT"
    assert view["$schema"] == "workflow.failed.v1"
    assert view["$source_id"] == "workflows.kafka"
    assert view["$event_id"] == "01H8X"
    assert view["$received_at"] == "2026-10-23T09:14:02.400Z"
    assert "$split_index" not in view


def test_split_index_is_present_only_when_split() -> None:
    letter = Letter(fields={}, raw={}, schema="s", split_index=3)
    assert letter.view()["$split_index"] == 3


def test_view_does_not_mutate_fields() -> None:
    """Conditions and templates receive the view; mutating it must not corrupt the Letter."""
    letter = _workflow_letter()
    v = letter.view()
    v["type"] = "TAMPERED"
    assert letter.fields["type"] == "WORKFLOW_FAILED"
    assert "payload" not in letter.fields


def test_payload_is_the_raw_value_not_a_copy() -> None:
    """Contract: `payload.<path>` is the raw payload, and only the raw payload."""
    letter = _workflow_letter()
    assert letter.view()["payload"] is letter.raw
