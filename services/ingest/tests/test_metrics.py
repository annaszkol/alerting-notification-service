"""Prometheus metrics of the ingest adapters (spec §10 point 3)."""

from prometheus_client import REGISTRY

from services.ingest.kafka_adapter import forward
from services.ingest.tests.test_http_ingest import TOKEN, Producer, client


def value(name, **labels):
    return REGISTRY.get_sample_value(name, labels) or 0.0


def test_http_accepted_and_rejected_are_counted_and_exposed():
    accepted, rejected = (
        value("ingest_events_accepted_total", adapter="http"),
        value("ingest_events_rejected_total", reason="unauthorized"),
    )
    c = client(Producer())
    assert c.post("/ingest/workflows", json={}, headers={"X-Source-Token": TOKEN}).status_code == 202
    assert c.post("/ingest/workflows", json={}, headers={"X-Source-Token": "wrong"}).status_code == 401

    assert value("ingest_events_accepted_total", adapter="http") == accepted + 1
    assert value("ingest_events_rejected_total", reason="unauthorized") == rejected + 1
    response = c.get("/metrics")
    assert response.status_code == 200
    assert "ingest_events_accepted_total" in response.text


def test_broker_failure_is_not_counted_as_accepted():
    accepted = value("ingest_events_accepted_total", adapter="http")
    c = client(Producer(error=RuntimeError("broker down")))
    assert c.post("/ingest/workflows", json={}, headers={"X-Source-Token": TOKEN}).status_code == 503
    assert value("ingest_events_accepted_total", adapter="http") == accepted


def test_kafka_adapter_counts_forwarded_records():
    class Record:
        topic, partition, offset, key, value, timestamp = "ext", 0, 7, None, b'{"a": 1}', 0

    before = value("ingest_events_accepted_total", adapter="kafka")
    assert forward([Record(), Record()], {"ext": "external"}, Producer(), "events.raw") == 2
    assert value("ingest_events_accepted_total", adapter="kafka") == before + 2
