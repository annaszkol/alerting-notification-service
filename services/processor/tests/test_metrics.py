"""Processor metrics (spec §10 point 3): processed events, alert changes, batch time, consumer lag."""

from collections import namedtuple

from prometheus_client import REGISTRY

from services.processor.batch import BatchResult
from services.processor.consumer import record, record_lag

TP = namedtuple("TP", "topic partition")


def value(name, **labels):
    return REGISTRY.get_sample_value(name, labels) or 0.0


def test_record_counts_a_batch():
    names = [("processor_events_total", {"result": "new"}), ("processor_events_total", {"result": "duplicate"}),
             ("processor_letters_total", {"mapped": "no"}), ("processor_alerts_total", {"change": "created"}),
             ("processor_alerts_total", {"change": "updated"}), ("processor_batch_seconds_count", {})]  # fmt: skip
    before = [value(n, **labels) for n, labels in names]
    record(BatchResult(new_events=5, duplicate_events=2, letters=4, unmapped=1, created=[1], updated=[2, 3]), 0.02)
    after = [value(n, **labels) for n, labels in names]
    assert [a - b for a, b in zip(after, before, strict=True)] == [5, 2, 1, 1, 2, 1]


def test_consumer_lag_per_partition():
    class Consumer:
        def assignment(self):
            return {TP("events.raw", 0), TP("events.raw", 1)}

        def end_offsets(self, partitions):
            return {TP("events.raw", 0): 100, TP("events.raw", 1): 40}

        def position(self, tp):
            return {0: 90, 1: 40}[tp.partition]

    record_lag(Consumer())
    assert value("processor_consumer_lag", partition="0") == 10
    assert value("processor_consumer_lag", partition="1") == 0


def test_lag_errors_never_raise():
    class Broken:
        def assignment(self):
            raise RuntimeError("not connected")

    record_lag(Broken())
