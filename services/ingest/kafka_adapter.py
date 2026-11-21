"""Kafka adapter (spec §2): reads external topics of `sources` rows with type `kafka` and writes envelopes to
`events.raw`. `python -m services.ingest.kafka_adapter`.

The event id is `<source>:<topic>:<partition>:<offset>`, stable across re-reads; the external offsets are committed
only after `events.raw` acknowledged the envelopes, so nothing is lost and repeats are dropped by the processor.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import threading
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

import psycopg

log = logging.getLogger("kafka-adapter")


def envelope(source_id: str, record: Any) -> dict[str, Any]:
    raw = record.value
    try:
        payload: Any = json.loads(raw)
        content_type = "application/json"
    except (TypeError, ValueError):
        payload = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        content_type = "text/plain"
    return {
        "eventId": f"{source_id}:{record.topic}:{record.partition}:{record.offset}",
        "sourceId": source_id,
        "receivedAt": datetime.now(UTC).isoformat(),
        "contentType": content_type,
        "payload": payload,
    }


def forward(records: Iterable[Any], topic_to_source: Mapping[str, str], producer: Any, topic: str) -> int:
    """Send envelopes for polled records and wait for every ack; the caller commits the external offsets after."""
    futures = []
    for record in records:
        source_id = topic_to_source.get(record.topic)
        if source_id is None:
            continue
        e = envelope(source_id, record)
        futures.append(producer.send(topic, key=e["eventId"].encode(), value=json.dumps(e).encode()))
    for future in futures:
        future.get(timeout=10)
    return len(futures)


def load_topics(conn: psycopg.Connection) -> dict[str, str]:
    rows = conn.execute(
        "SELECT config->>'topic', id FROM sources WHERE enabled AND type = 'kafka' AND config ? 'topic'"
    ).fetchall()
    return {topic: source_id for topic, source_id in rows}


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(name)s %(levelname)s %(message)s"
    )
    from kafka import KafkaConsumer, KafkaProducer

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    brokers = os.environ.get("KAFKA_BROKERS", "localhost:19092").split(",")
    out_topic = os.environ.get("EVENTS_RAW_TOPIC", "events.raw")
    producer = KafkaProducer(bootstrap_servers=brokers, acks="all")
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        topics: dict[str, str] = {}
        while not stop.is_set() and not topics:
            topics = load_topics(conn) if conn.execute("SELECT to_regclass('sources')").fetchone()[0] else {}
            if not topics:
                stop.wait(10)
        if stop.is_set():
            return
        log.info("forwarding %s", topics)
        consumer = KafkaConsumer(
            *topics, bootstrap_servers=brokers, group_id="ingest-kafka-adapter", enable_auto_commit=False,
            auto_offset_reset="earliest",
        )  # fmt: skip
        while not stop.is_set():
            polled = consumer.poll(timeout_ms=1000)
            records = [r for batch in polled.values() for r in batch]
            if records:
                forward(records, topics, producer, out_topic)
                consumer.commit()
        consumer.close()
    producer.close()


if __name__ == "__main__":
    main()
