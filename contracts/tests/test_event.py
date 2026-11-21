"""Sanity checks for the EventEnvelope contract (owner A, spec §3)."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

import pytest

from contracts.event import KAFKA_TOPIC, PARTITION_KEY_FIELD, EventEnvelope


def test_topic_and_partition_key_are_stable() -> None:
    """Consumers and producers rely on these constants; they are part of the contract."""
    assert KAFKA_TOPIC == "events.raw"
    assert PARTITION_KEY_FIELD == "event_id"


def test_envelope_is_frozen() -> None:
    env = EventEnvelope(
        event_id="01H8X",
        source_id="workflows.kafka",
        received_at=datetime(2026, 10, 23, 9, 14, 2, tzinfo=UTC),
        content_type="application/json",
        payload={"type": "WORKFLOW_FAILED"},
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        env.event_id = "other"  # type: ignore[misc]


def test_envelope_accepts_any_payload() -> None:
    """JSON dicts, lists (split), and raw strings are all valid payloads."""
    now = datetime(2026, 10, 23, 9, 14, 2, tzinfo=UTC)
    for payload in ({"a": 1}, [{"a": 1}], "raw log line", 42, None):
        EventEnvelope(
            event_id="e1",
            source_id="s",
            received_at=now,
            content_type="application/json",
            payload=payload,
        )


def test_envelope_carries_no_raw_provider_fields() -> None:
    """Contract: no `raw_headers`, `provider`, or similar - adapters strip those.

    This test exists to catch a reviewer slipping provider-specific fields into the
    envelope in a later PR.
    """
    field_names = set(EventEnvelope.__dataclass_fields__)
    assert field_names == {
        "event_id",
        "source_id",
        "received_at",
        "content_type",
        "payload",
    }
