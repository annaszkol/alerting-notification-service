"""Event envelope contract (owner A, spec §3 ingest, spec §5 processor).

Every adapter wraps a raw message in `EventEnvelope` and writes it to Kafka `events.raw`.
The processor consumes envelopes, never raw provider payloads. Adding a source or a
schema is a new adapter or a new row in `schemas` - it never changes this type.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

KAFKA_TOPIC = "events.raw"
PARTITION_KEY_FIELD = "event_id"


@dataclass(frozen=True)
class EventEnvelope:
    """One message as it enters the pipeline.

    `event_id` is assigned at ingest and is stable across replays: it is the
    idempotency key the processor inserts into `processed_events` before doing any
    work, so a Kafka replay or a retried HTTP ingest never produces a duplicate alert.

    `content_type` tells the processor which parsers to try:
        "application/json" - `payload` is the already-decoded value (dict or list)
        "text/logfmt"      - `payload` is the raw line
        "text/plain"       - `payload` is the raw text (regex parsers)
    The processor may still fall back to JSON parsing for unknown content types; the
    value is a hint, not a strict contract.

    `payload` is `Any`: a dict, a list (for split), or a string. Consumers that need
    structure must run it through a parser first.
    """

    event_id: str
    source_id: str
    received_at: datetime
    content_type: str
    payload: Any
