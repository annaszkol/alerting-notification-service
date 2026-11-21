"""Processor process (owner A; written by B): `python -m services.processor`.

Env: DATABASE_URL, KAFKA_BROKERS, EVENTS_RAW_TOPIC (events.raw), PROCESSOR_GROUP (processor), PROCESSOR_BATCH (500).
Scale by running more instances: Kafka splits the topic's partitions between them (spec §10).
"""

from __future__ import annotations

import logging
import os
import signal
import threading
import time

import psycopg

from services.processor import config as config_store
from services.processor.consumer import decode, handle

log = logging.getLogger("processor")
CONFIG_POLL_SECONDS = 5  # spec §4: processors also poll config_version every 5 s


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(name)s %(levelname)s %(message)s"
    )
    from kafka import KafkaConsumer  # imported here so tests do not need a broker client

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    consumer = KafkaConsumer(
        os.environ.get("EVENTS_RAW_TOPIC", "events.raw"),
        bootstrap_servers=os.environ.get("KAFKA_BROKERS", "localhost:19092").split(","),
        group_id=os.environ.get("PROCESSOR_GROUP", "processor"),
        enable_auto_commit=False,  # offsets only after the DB transaction committed
        auto_offset_reset="earliest",
        max_poll_records=int(os.environ.get("PROCESSOR_BATCH", "500")),
    )
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        while not stop.is_set() and not config_store.tables_ready(conn):
            log.warning("waiting for A's tables (%s); not reading Kafka yet", ", ".join(config_store.REQUIRED_TABLES))
            conn.commit()
            stop.wait(30)
        config = config_store.load(conn)
        conn.commit()
        checked = time.monotonic()
        log.info("processor started")
        while not stop.is_set():
            if time.monotonic() - checked >= CONFIG_POLL_SECONDS:
                if config_store.current_version(conn) != config.version:
                    config = config_store.load(conn)
                conn.commit()
                checked = time.monotonic()
            polled = consumer.poll(timeout_ms=1000)
            records = [r for batch in polled.values() for r in batch]
            if not records:
                continue
            envelopes = [e for e in (decode(r.value) for r in records) if e is not None]
            if len(envelopes) < len(records):
                log.warning("%d malformed envelopes skipped", len(records) - len(envelopes))
            result = handle(conn, envelopes, config, consumer)
            log.info(
                "batch: %d new, %d duplicate, %d alerts created, %d updated, %d resolved, %d unmapped",
                result.new_events, result.duplicate_events, len(result.created), len(result.updated),
                len(result.resolved), result.unmapped,
            )  # fmt: skip
    consumer.close()


if __name__ == "__main__":
    main()
