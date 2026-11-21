"""Kafka side of the processor: decode envelopes, run a batch, commit offsets only after the DB commit."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any, Protocol

import psycopg

from libs import metrics
from libs.conditions import Envelope
from services.processor.batch import BatchResult, process_batch
from services.processor.config import Config

log = logging.getLogger("processor")

RETRYABLE = (psycopg.errors.DeadlockDetected, psycopg.errors.SerializationFailure)
MAX_TX_ATTEMPTS = 5


def decode(value: bytes | str | dict[str, Any]) -> Envelope | None:
    """Envelope JSON (spec §2 `{eventId, sourceId, receivedAt, contentType, payload}`; snake_case accepted too)."""
    try:
        doc = value if isinstance(value, dict) else json.loads(value)
        received = doc.get("receivedAt") or doc.get("received_at")
        when = datetime.fromisoformat(str(received).replace("Z", "+00:00")) if received else datetime.now(UTC)
        return Envelope(
            event_id=str(doc.get("eventId") or doc["event_id"]),
            source_id=str(doc.get("sourceId") or doc["source_id"]),
            received_at=when if when.tzinfo else when.replace(tzinfo=UTC),
            content_type=doc.get("contentType") or doc.get("content_type") or "application/json",
            payload=doc.get("payload"),
        )
    except (ValueError, KeyError, TypeError):
        return None


class Committer(Protocol):
    def commit(self) -> Any: ...


def handle(
    conn: psycopg.Connection,
    envelopes: Sequence[Envelope],
    config: Config,
    consumer: Committer,
    sleep: Callable[[float], None] = time.sleep,
) -> BatchResult:
    """One batch: one transaction (retried on deadlock / serialization failure), then the Kafka offset commit.

    If the process dies between the DB commit and the offset commit, the batch is read again and
    `processed_events` turns it into a no-op (spec §5)."""
    started = time.perf_counter()
    for attempt in range(1, MAX_TX_ATTEMPTS + 1):
        try:
            with conn.transaction():
                result = process_batch(conn, envelopes, config)
            break
        except RETRYABLE as exc:
            if attempt == MAX_TX_ATTEMPTS:
                raise
            log.warning("batch retry %d after %s", attempt, type(exc).__name__)
            sleep(0.05 * 2**attempt)
    consumer.commit()
    record(result, time.perf_counter() - started)
    return result


def record(result: BatchResult, seconds: float) -> None:
    """Metrics of one committed batch."""
    metrics.PROCESSOR_BATCH_SECONDS.observe(seconds)
    metrics.PROCESSOR_EVENTS.labels("new").inc(result.new_events)
    metrics.PROCESSOR_EVENTS.labels("duplicate").inc(result.duplicate_events)
    metrics.PROCESSOR_LETTERS.labels("yes").inc(result.letters)
    metrics.PROCESSOR_LETTERS.labels("no").inc(result.unmapped)
    metrics.PROCESSOR_ALERTS.labels("created").inc(len(result.created))
    metrics.PROCESSOR_ALERTS.labels("updated").inc(len(result.updated))
    metrics.PROCESSOR_ALERTS.labels("resolved").inc(len(result.resolved))


def record_lag(consumer: Any) -> None:
    """Consumer lag per assigned partition: end offset minus our position (kafka-python consumer API)."""
    try:
        partitions = consumer.assignment()
        if not partitions:
            return
        ends = consumer.end_offsets(list(partitions))
        for tp in partitions:
            metrics.PROCESSOR_LAG.labels(str(tp.partition)).set(max(0, ends[tp] - consumer.position(tp)))
    except Exception as exc:  # noqa: BLE001 - metrics must never stop processing
        log.debug("consumer lag not measured: %s", exc)
