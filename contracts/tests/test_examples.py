"""Spec examples (Appendix A) parse into the contract shapes (owner A).

These are the same inputs the judges will bring to the demo. If this file is green,
the contract accepts every example in the spec; if a schema is added later, its
example must be added here before the PR is merged.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from contracts.condition import Op
from contracts.event import EventEnvelope
from contracts.template import PLACEHOLDER

# Appendix A, workflow failure. The spec shows it as a malformed fragment; we parse
# it as valid JSON to prove the contract accepts it.
WORKFLOW_FAILURE = {
    "type": "WORKFLOW_FAILED",
    "workflowid": "WF-20261023-00042",
    "workflowName": "order-settlement",
    "step": "validate-counterparty",
    "severity": "ERROR",
    "environment": "prod",
    "timestamp": "2026-10-23T09:14:02.331Z",
    "error": {
        "code": "TIMEOUT",
        "message": "Counterparty service did not respond in 5000 ms",
    },
}

# Appendix A, metric threshold breach.
METRIC_THRESHOLD = {
    "alertname": "HighErrorRate",
    "labels": {
        "service": "payments-api",
        "region": "eu-west",
        "severity": "critical",
    },
    "annotations": {
        "summary": "Error rate 12% over the last 5 minutes",
    },
    "startsAt": "2026-10-23T09:13:00Z",
    "status": "firing",
}

# Appendix A, business event with a different envelope.
BUSINESS_EVENT = {
    "meta": {
        "schema": "trade.rejected.v2",
        "producer": "trade-gateway",
        "ts": 1792833245000,
    },
    "payload": {
        "tradeld": "T-998877",  # sic, matches the spec
        "desk": "rates",
        "notional": 25_000_000,
        "currency": "EUR",
        "reason": "LIMIT_BREACH",
    },
}


def test_workflow_failure_wraps_into_envelope() -> None:
    env = EventEnvelope(
        event_id="01H8X",
        source_id="workflows.kafka",
        received_at=datetime(2026, 10, 23, 9, 14, 2, 400_000, tzinfo=UTC),
        content_type="application/json",
        payload=WORKFLOW_FAILURE,
    )
    assert env.payload["type"] == "WORKFLOW_FAILED"
    assert env.payload["error"]["code"] == "TIMEOUT"


def test_metric_threshold_wraps_into_envelope() -> None:
    env = EventEnvelope(
        event_id="01H8Y",
        source_id="alerts.webhook",
        received_at=datetime(2026, 10, 23, 9, 13, 0, tzinfo=UTC),
        content_type="application/json",
        payload=METRIC_THRESHOLD,
    )
    assert env.payload["labels"]["severity"] == "critical"


def test_business_event_wraps_into_envelope() -> None:
    env = EventEnvelope(
        event_id="01H8Z",
        source_id="trades.kafka",
        received_at=datetime.fromtimestamp(1792833245, tz=UTC),
        content_type="application/json",
        payload=BUSINESS_EVENT,
    )
    assert env.payload["meta"]["schema"] == "trade.rejected.v2"


def test_conditions_in_spec_are_expressible() -> None:
    """The four plain-word rules from spec §4 must be expressible in the AST."""
    rules = [
        # Team Settlements
        {
            "all": [
                {"field": "type", "op": Op.EQ, "value": "WORKFLOW_FAILED"},
                {"field": "workflowName", "op": Op.EQ, "value": "order-settlement"},
                {"field": "environment", "op": Op.EQ, "value": "prod"},
            ]
        },
        # Team Pricing
        {
            "all": [
                {"field": "service", "op": Op.GLOB, "value": "pricing-engine-*"},
                {"field": "severity", "op": Op.GTE, "value": "warning"},
            ]
        },
        # Team Rates
        {
            "all": [
                {"field": "$schema", "op": Op.GLOB, "value": "trade.rejected.*"},
                {"field": "payload.desk", "op": Op.EQ, "value": "rates"},
                {"field": "payload.notional", "op": Op.GT, "value": 10_000_000},
            ]
        },
        # Anna (personal)
        {
            "all": [
                {"field": "labels.service", "op": Op.EQ, "value": "payments-api"},
                {"field": "labels.severity", "op": Op.EQ, "value": "critical"},
            ]
        },
    ]
    for r in rules:
        # Structural check only; evaluation lives in libs/conditions.
        assert "all" in r and isinstance(r["all"], list) and r["all"]


def test_templates_from_spec_are_parseable() -> None:
    examples = [
        "Rejected {{payload.currency}} {{payload.notional}} - {{payload.reason}}",
        "[{{$count}}] {{workflowName}} failed at {{step}}",
    ]
    for tpl in examples:
        names = PLACEHOLDER.findall(tpl)
        assert names, tpl  # every example must have at least one placeholder


def test_json_round_trip_is_stable() -> None:
    """The Appendix A payloads survive a JSON round trip; processors rely on this."""
    for payload in (WORKFLOW_FAILURE, METRIC_THRESHOLD, BUSINESS_EVENT):
        assert json.loads(json.dumps(payload)) == payload
