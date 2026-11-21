"""HTTP ingest adapter (spec §2, §11): token, size limit, envelope, 202 only after the Kafka ack."""

import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from services.ingest import app as ingest
from services.ingest.app import HttpSource, SourceCache, create_app

TOKEN = "s3cret-token"


class Future:
    def __init__(self, error=None):
        self.error = error

    def get(self, timeout):
        if self.error:
            raise self.error
        return "metadata"


class Producer:
    def __init__(self, error=None):
        self.sent, self.error = [], error

    def send(self, topic, key, value):
        self.sent.append((topic, key.decode(), json.loads(value)))
        return Future(self.error)


def client(producer=None):
    sources = SourceCache(lambda: {"workflows": HttpSource("workflows", hashlib.sha256(TOKEN.encode()).hexdigest())})
    return TestClient(create_app(sources=sources, producer=producer or Producer()))


def test_accepts_event_and_writes_envelope():
    producer = Producer()
    response = client(producer).post(
        "/ingest/workflows", json={"type": "WORKFLOW_FAILED"}, headers={"X-Source-Token": TOKEN}
    )
    assert response.status_code == 202
    [(topic, key, envelope)] = producer.sent
    assert topic == "events.raw"
    assert key == envelope["eventId"] == response.json()["eventId"]
    assert (envelope["sourceId"], envelope["contentType"], envelope["payload"]) == (
        "workflows", "application/json", {"type": "WORKFLOW_FAILED"},
    )  # fmt: skip
    assert envelope["receivedAt"].endswith("+00:00")


def test_bearer_token_and_text_payload():
    producer = Producer()
    response = client(producer).post(
        "/ingest/workflows",
        content="level=WARN msg=x",
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "text/plain"},
    )
    assert response.status_code == 202
    assert producer.sent[0][2]["payload"] == "level=WARN msg=x"


@pytest.mark.parametrize(
    ("path", "headers"),
    [
        ("/ingest/workflows", {}),
        ("/ingest/workflows", {"X-Source-Token": "wrong"}),
        ("/ingest/unknown", {"X-Source-Token": TOKEN}),  # same answer: does not reveal which sources exist
    ],
)
def test_rejects_without_a_valid_token(path, headers):
    producer = Producer()
    response = client(producer).post(path, json={}, headers=headers)
    assert response.status_code == 401
    assert producer.sent == []


def test_idempotency_key_gives_a_stable_event_id():
    producer = Producer()
    c = client(producer)
    headers = {"X-Source-Token": TOKEN, "Idempotency-Key": "abc-1"}
    first = c.post("/ingest/workflows", json={}, headers=headers).json()["eventId"]
    second = c.post("/ingest/workflows", json={}, headers=headers).json()["eventId"]
    assert first == second == "workflows:abc-1"  # the processor's processed_events makes the retry a no-op


def test_payload_limit_and_invalid_json(monkeypatch):
    monkeypatch.setattr(ingest, "MAX_BYTES", 100)
    c = client()
    big = c.post("/ingest/workflows", json={"x": "y" * 200}, headers={"X-Source-Token": TOKEN})
    assert big.status_code == 413
    bad = c.post(
        "/ingest/workflows", content="{nope", headers={"X-Source-Token": TOKEN, "Content-Type": "application/json"}
    )
    assert bad.status_code == 422


def test_kafka_failure_is_503_so_the_sender_retries():
    response = client(Producer(error=TimeoutError("no ack"))).post(
        "/ingest/workflows", json={}, headers={"X-Source-Token": TOKEN}
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "unavailable"


def test_source_cache_reloads_unknown_ids_and_survives_db_errors():
    calls = []

    def load():
        calls.append(1)
        return (
            {"a": HttpSource("a", None)}
            if len(calls) == 1
            else {"a": HttpSource("a", None), "b": HttpSource("b", None)}
        )

    cache = SourceCache(load, ttl=60)
    assert cache.get("a") is not None
    assert cache.get("b") is not None  # a new source is picked up without waiting for the TTL
    assert len(calls) == 2
