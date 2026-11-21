"""Log tailer and Kafka adapter (spec §2): stable event ids, offsets after the ack, rotation."""

import json
import os
from types import SimpleNamespace

import psycopg
import pytest

from db.testing import TEST_DATABASE_URL, temporary_database
from mocks.generator.__main__ import main as generator
from services.ingest import tailer
from services.ingest.kafka_adapter import envelope, forward


class Producer:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def send(self, topic, key, value):
        self.sent.append((topic, key.decode(), json.loads(value)))
        return SimpleNamespace(get=lambda timeout: (_ for _ in ()).throw(TimeoutError()) if self.fail else None)


def test_read_lines_only_complete_ones(tmp_path):
    log = tmp_path / "pricing.log"
    log.write_text('level=WARN msg="a"\n\nlevel=ERROR msg="b"\nlevel=INFO msg="partial')
    inode, offset, lines = tailer.read_lines(str(log), None, 0)
    assert [text for _, text in lines] == ['level=WARN msg="a"', 'level=ERROR msg="b"']
    assert offset == len('level=WARN msg="a"\n\nlevel=ERROR msg="b"\n')
    with log.open("a") as f:
        f.write('"\n')
    _, _, rest = tailer.read_lines(str(log), inode, offset)
    assert [text for _, text in rest] == ['level=INFO msg="partial"']


def test_rotation_and_truncation_start_over(tmp_path):
    log = tmp_path / "x.log"
    log.write_text("a=1\nb=2\n")
    inode, offset, _ = tailer.read_lines(str(log), None, 0)
    log.write_text("c=3\n")  # truncated: shorter than the saved offset
    assert [t for _, t in tailer.read_lines(str(log), inode, offset)[2]] == ["c=3"]
    os.remove(log)
    log.write_text("d=4\ne=5\nf=6\n")  # rotated: new inode
    assert len(tailer.read_lines(str(log), inode, 4)[2]) == 3


def test_envelopes_have_stable_ids():
    source = tailer.LogSource("pricing-logs", "/x", "logfmt")
    [e] = tailer.envelopes(source, 42, [(128, "level=WARN")])
    assert (e["eventId"], e["contentType"], e["payload"]) == ("pricing-logs:42:128", "text/logfmt", "level=WARN")


@pytest.fixture
def db(tmp_path):
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    with (
        temporary_database("test_tailer", migrate=True) as conninfo,
        psycopg.connect(conninfo, autocommit=True) as conn,
    ):
        yield conn


@pytest.mark.db
def test_tail_once_saves_offset_after_the_ack_and_never_resends(db, tmp_path):
    log = tmp_path / "pricing.log"
    source = tailer.LogSource("pricing-logs", str(log), "logfmt")
    generator(["pricing", "--lines", "3", "--file", str(log)])

    failing = Producer(fail=True)
    with pytest.raises(TimeoutError):
        tailer.tail_once(db, failing, source, "events.raw")
    assert tailer.get_offset(db, source) == (None, 0)  # nothing acknowledged -> offset not moved

    producer = Producer()
    assert tailer.tail_once(db, producer, source, "events.raw") == 3
    assert tailer.tail_once(db, producer, source, "events.raw") == 0  # restart-safe: offset saved
    ids = [e["eventId"] for _, _, e in producer.sent]
    assert ids == [e["eventId"] for _, _, e in failing.sent]  # the retry reused the same event ids
    assert {e["sourceId"] for _, _, e in producer.sent} == {"pricing-logs"}


@pytest.mark.db
def test_load_logfile_sources(db):
    db.execute("INSERT INTO sources (id, type, parser, config) VALUES ('p', 'logfile', 'logfmt', '{\"path\": \"/l\"}'),"
               " ('h', 'http', 'json', '{}')")  # fmt: skip
    assert tailer.load_sources(db) == [tailer.LogSource("p", "/l", "logfmt")]


def test_kafka_adapter_wraps_and_waits_for_acks():
    records = [
        SimpleNamespace(topic="workflow-events", partition=3, offset=17, value=b'{"type": "WORKFLOW_FAILED"}'),
        SimpleNamespace(topic="workflow-events", partition=3, offset=18, value=b"plain text"),
        SimpleNamespace(topic="other", partition=0, offset=1, value=b"{}"),
    ]
    producer = Producer()
    assert forward(records, {"workflow-events": "workflows-kafka"}, producer, "events.raw") == 2
    first, second = (e for _, _, e in producer.sent)
    assert (first["eventId"], first["payload"]) == ("workflows-kafka:workflow-events:3:17", {"type": "WORKFLOW_FAILED"})
    assert (second["contentType"], second["payload"]) == ("text/plain", "plain text")
    assert envelope("s", records[0])["eventId"] == first["eventId"].replace("workflows-kafka", "s")
    with pytest.raises(TimeoutError):
        forward(records[:1], {"workflow-events": "w"}, Producer(fail=True), "events.raw")


def test_generator_payloads(monkeypatch):
    sent = []
    monkeypatch.setattr(
        "mocks.generator.__main__.post", lambda source, payload, key=None: sent.append((source, payload)) or 202
    )
    generator(["workflow"])
    generator(["trade", "--notional", "5000000"])
    generator(["high-error-rate", "--resolved"])
    generator(["burst", "--count", "5", "--workers", "2"])
    assert [s for s, _ in sent[:3]] == ["workflows", "trades", "monitoring"]
    assert sent[1][1]["payload"]["notional"] == 5000000
    assert sent[2][1]["status"] == "resolved"
    burst = [p for s, p in sent[3:]]
    assert len(burst) == 5 and all(p == burst[0] for p in burst)  # identical events
