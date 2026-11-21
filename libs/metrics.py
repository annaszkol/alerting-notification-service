"""Prometheus metrics (spec §10 point 3: events/s accepted and processed, consumer lag, notification queue size).

All metrics live in the default registry. The ingest HTTP app exposes `/metrics` on its own port; worker processes
start a small HTTP server on `METRICS_PORT` (defaults below, see README "Metrics"). Rates (events/s) come from
Prometheus: `rate(processor_events_total[1m])`.
"""

from __future__ import annotations

import logging
import os

from prometheus_client import Counter, Gauge, Histogram, start_http_server

log = logging.getLogger("metrics")

# default METRICS_PORT per worker process (inside compose every container may use its own default)
PORTS = {"processor": 9101, "delivery": 9102, "executor": 9103, "tailer": 9104, "kafka_adapter": 9105}

# --- ingest ---------------------------------------------------------------------------------------------
INGEST_ACCEPTED = Counter(
    "ingest_events_accepted_total", "Events acknowledged by Kafka in events.raw", ["adapter"]
)  # http | logfile | kafka
INGEST_REJECTED = Counter(
    "ingest_events_rejected_total", "HTTP ingest requests refused", ["reason"]
)  # unauthorized | too_large | invalid | unavailable

# --- processor ------------------------------------------------------------------------------------------
PROCESSOR_EVENTS = Counter("processor_events_total", "Events read by the processor", ["result"])  # new | duplicate
PROCESSOR_LETTERS = Counter("processor_letters_total", "Letters after mapping", ["mapped"])  # yes | no
PROCESSOR_ALERTS = Counter("processor_alerts_total", "Alert changes", ["change"])  # created | updated | resolved
PROCESSOR_BATCH_SECONDS = Histogram(
    "processor_batch_seconds",
    "Duration of one batch: DB transaction (with retries) and Kafka offset commit",
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)
PROCESSOR_LAG = Gauge("processor_consumer_lag", "Messages behind the end of events.raw", ["partition"])

# --- delivery -------------------------------------------------------------------------------------------
DELIVERY_ATTEMPTS = Counter(
    "delivery_attempts_total", "Send attempts by channel and outcome", ["channel", "outcome"]
)  # outcome: sent | pending (retry) | dead
NOTIFICATION_QUEUE = Gauge("notifications_queue", "Notifications by status (counted in Postgres)", ["status"])
NOTIFICATION_STATUSES = ("pending", "sending", "sent", "dead", "held")

# --- executor -------------------------------------------------------------------------------------------
EXECUTOR_STEPS = Counter("executor_steps_total", "Processed runbook step runs by outcome", ["outcome"])


def serve(process: str) -> None:
    """Start the metrics HTTP server of a worker process on METRICS_PORT (default from PORTS)."""
    port = int(os.environ.get("METRICS_PORT", str(PORTS[process])))
    try:
        start_http_server(port)
        log.info("metrics on :%d/metrics", port)
    except OSError as exc:  # a port clash (two instances on one host) must not stop the worker
        log.warning("metrics server not started on :%d: %s", port, exc)
