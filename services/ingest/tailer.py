"""Log tailer (spec §2): one process per log file, new lines -> envelopes on `events.raw`, offset in Postgres.

`python -m services.ingest.tailer` follows every enabled `sources` row of type `logfile` (`config.path`).
- Only complete lines are sent; a partial last line waits for its newline.
- The event id is `<source>:<inode>:<byte offset>`, so re-reading after a crash produces the same ids and the
  processor's `processed_events` drops the repeats (at-least-once delivery, exactly-once effect).
- The offset is saved only after Kafka acknowledged the lines. A new inode (rotation) or a file shorter than the
  saved offset (truncation) starts from 0.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import psycopg

log = logging.getLogger("tailer")
MAX_LINE = 64 * 1024
READ_CHUNK = 1024 * 1024


@dataclass(frozen=True)
class LogSource:
    id: str
    path: str
    parser: str = "logfmt"


def load_sources(conn: psycopg.Connection) -> list[LogSource]:
    rows = conn.execute(
        "SELECT id, config->>'path', parser FROM sources WHERE enabled AND type = 'logfile' AND config ? 'path'"
    ).fetchall()
    return [LogSource(r[0], r[1], r[2]) for r in rows]


def get_offset(conn: psycopg.Connection, source: LogSource) -> tuple[int | None, int]:
    row = conn.execute(
        'SELECT inode, "offset" FROM tailer_offsets WHERE source_id = %s AND file = %s', (source.id, source.path)
    ).fetchone()
    return (row[0], row[1]) if row else (None, 0)


def save_offset(conn: psycopg.Connection, source: LogSource, inode: int, offset: int) -> None:
    conn.execute(
        """INSERT INTO tailer_offsets (source_id, file, inode, "offset", updated_at) VALUES (%s, %s, %s, %s, now())
           ON CONFLICT (source_id, file) DO UPDATE SET inode = EXCLUDED.inode, "offset" = EXCLUDED."offset",
                                                     updated_at = now()""",
        (source.id, source.path, inode, offset),
    )


def read_lines(path: str, inode: int | None, offset: int) -> tuple[int, int, list[tuple[int, str]]]:
    """(inode, new offset, [(line start offset, line)]) of complete lines after `offset`."""
    stat = os.stat(path)
    if inode != stat.st_ino or stat.st_size < offset:  # rotated or truncated: start over
        offset = 0
    lines: list[tuple[int, str]] = []
    with open(path, "rb") as f:
        f.seek(offset)
        data = f.read(READ_CHUNK)
    position = offset
    for raw in data.split(b"\n")[:-1]:  # the last piece has no newline yet (or is empty)
        start, position = position, position + len(raw) + 1
        text = raw[:MAX_LINE].decode("utf-8", "replace").rstrip("\r")
        if text.strip():
            lines.append((start, text))
    return stat.st_ino, position, lines


def envelopes(source: LogSource, inode: int, lines: list[tuple[int, str]]) -> Iterator[dict[str, Any]]:
    content_type = "text/logfmt" if source.parser == "logfmt" else "text/plain"
    now = datetime.now(UTC).isoformat()
    for start, text in lines:
        yield {
            "eventId": f"{source.id}:{inode}:{start}",
            "sourceId": source.id,
            "receivedAt": now,
            "contentType": content_type,
            "payload": text,
        }


def tail_once(conn: psycopg.Connection, producer: Any, source: LogSource, topic: str) -> int:
    """Send new lines of one file; returns how many. Offset saved only after every line was acknowledged."""
    if not os.path.exists(source.path):
        return 0
    inode, offset = get_offset(conn, source)
    inode, new_offset, lines = read_lines(source.path, inode, offset)
    futures = [
        producer.send(topic, key=e["eventId"].encode(), value=json.dumps(e).encode())
        for e in envelopes(source, inode, lines)
    ]
    for future in futures:
        future.get(timeout=10)
    if lines or new_offset != offset:
        save_offset(conn, source, inode, new_offset)
    return len(lines)


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(name)s %(levelname)s %(message)s"
    )
    from kafka import KafkaProducer

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    topic = os.environ.get("EVENTS_RAW_TOPIC", "events.raw")
    brokers = os.environ.get("KAFKA_BROKERS", "localhost:19092").split(",")
    producer = KafkaProducer(bootstrap_servers=brokers, acks="all")
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        while not stop.is_set() and conn.execute("SELECT to_regclass('tailer_offsets')").fetchone()[0] is None:
            stop.wait(10)
        sources: list[LogSource] = []
        loaded = -1e9
        while not stop.is_set():
            if time.monotonic() - loaded > 30:  # new logfile sources are picked up without a restart
                sources, loaded = load_sources(conn), time.monotonic()
                log.info("tailing %s", [s.path for s in sources])
            sent = 0
            for source in sources:
                try:
                    sent += tail_once(conn, producer, source, topic)
                except Exception:  # noqa: BLE001 - keep tailing the other files
                    log.exception("tailing %s failed", source.path)
            if not sent:
                stop.wait(0.5)
    producer.close()


if __name__ == "__main__":
    main()
