"""HTTP ingest adapter (spec §2 "Adaptery", §11 "Bezpieczeństwo").

`POST /ingest/{sourceId}` with the source's token (`X-Source-Token: <token>` or `Authorization: Bearer <token>`).
The body is any JSON (or text for log-like sources), at most `INGEST_MAX_BYTES`. The adapter wraps it in the
envelope `{eventId, sourceId, receivedAt, contentType, payload}` (contracts/event.py) and writes it to Kafka
`events.raw` keyed by the event id; it answers `202` only after Kafka acknowledged the write, otherwise `503` so the
sender retries. An `Idempotency-Key` header becomes the event id, so a retried POST is processed once.

Sources are rows in `sources` (type `http`, token stored as SHA-256), cached for a few seconds: a new source needs
no code and no restart.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import threading
import time
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import psycopg
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from libs import metrics

log = logging.getLogger("ingest")

TOPIC = os.environ.get("EVENTS_RAW_TOPIC", "events.raw")
MAX_BYTES = int(os.environ.get("INGEST_MAX_BYTES", str(256 * 1024)))
SOURCE_CACHE_SECONDS = 5.0
KAFKA_ACK_TIMEOUT = 10.0


class Producer(Protocol):
    def send(self, topic: str, key: bytes, value: bytes) -> Any: ...  # returns a future with .get(timeout)


@dataclass(frozen=True)
class HttpSource:
    id: str
    token_sha256: str | None


class SourceCache:
    """`sources` rows of type http, re-read at most every few seconds."""

    def __init__(self, load: Callable[[], dict[str, HttpSource]], ttl: float = SOURCE_CACHE_SECONDS) -> None:
        self._load, self._ttl = load, ttl
        self._sources: dict[str, HttpSource] = {}
        self._loaded_at = -1e9
        self._lock = threading.Lock()

    def get(self, source_id: str) -> HttpSource | None:
        with self._lock:
            if time.monotonic() - self._loaded_at > self._ttl or source_id not in self._sources:
                try:
                    self._sources = self._load()
                    self._loaded_at = time.monotonic()
                except psycopg.Error as exc:
                    log.error("cannot read sources: %s", exc)
            return self._sources.get(source_id)


def load_http_sources(database_url: str) -> Callable[[], dict[str, HttpSource]]:
    def load() -> dict[str, HttpSource]:
        with psycopg.connect(database_url, autocommit=True) as conn:
            rows = conn.execute("SELECT id, token_sha256 FROM sources WHERE enabled AND type = 'http'").fetchall()
        return {r[0]: HttpSource(r[0], r[1]) for r in rows}

    return load


def _token(request: Request) -> str | None:
    if token := request.headers.get("x-source-token"):
        return token
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    return value.strip() if scheme.lower() == "bearer" and value else None


def _error(status: int, code: str, message: str) -> JSONResponse:
    metrics.INGEST_REJECTED.labels(code).inc()
    return JSONResponse({"error": {"code": code, "message": message, "details": None}}, status_code=status)


def create_app(
    sources: SourceCache | None = None,
    producer: Producer | None = None,
    make_producer: Callable[[], Producer] | None = None,
) -> FastAPI:
    state: dict[str, Any] = {"producer": producer}

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if state["producer"] is None and make_producer is not None:
            state["producer"] = make_producer()
        yield
        close = getattr(state["producer"], "close", None)
        if close:
            close()

    app = FastAPI(title="ingest", lifespan=lifespan)
    cache = sources

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "service": "ingest"}

    @app.get("/metrics", include_in_schema=False)
    def metrics_endpoint() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.post("/ingest/{source_id}", status_code=202)
    async def ingest(source_id: str, request: Request):
        source = cache.get(source_id) if cache else None
        token = _token(request)
        expected = source.token_sha256 if source else None
        given = hashlib.sha256(token.encode()).hexdigest() if token else ""
        # same answer for unknown source and wrong token: do not reveal which sources exist
        if not source or not expected or not hmac.compare_digest(given, expected):
            return _error(401, "unauthorized", "unknown source or wrong token")

        declared = int(request.headers.get("content-length") or 0)
        if declared > MAX_BYTES:
            return _error(413, "too_large", f"payload over {MAX_BYTES} bytes")
        body = await request.body()
        if len(body) > MAX_BYTES:
            return _error(413, "too_large", f"payload over {MAX_BYTES} bytes")

        content_type = (request.headers.get("content-type") or "application/json").split(";")[0].strip().lower()
        if content_type == "application/json":
            try:
                payload: Any = json.loads(body or b"null")
            except ValueError:
                return _error(422, "invalid", "body is not valid JSON")
        else:
            payload = body.decode("utf-8", "replace")

        event_id = request.headers.get("idempotency-key") or str(uuid.uuid4())
        if len(event_id) > 200:
            return _error(422, "invalid", "Idempotency-Key longer than 200 characters")
        envelope = {
            "eventId": f"{source_id}:{event_id}" if request.headers.get("idempotency-key") else event_id,
            "sourceId": source_id,
            "receivedAt": datetime.now(UTC).isoformat(),
            "contentType": content_type,
            "payload": payload,
        }
        producer_ = state["producer"]
        if producer_ is None:
            return _error(503, "unavailable", "event broker not connected")
        try:
            future = producer_.send(TOPIC, key=envelope["eventId"].encode(), value=json.dumps(envelope).encode())
            future.get(timeout=KAFKA_ACK_TIMEOUT)  # 202 only after Kafka acknowledged the write (spec §2)
        except Exception as exc:  # noqa: BLE001 - any broker error: the sender retries
            log.warning("kafka write failed: %s", exc)
            return _error(503, "unavailable", "event broker unavailable, retry later")
        metrics.INGEST_ACCEPTED.labels("http").inc()
        return {"eventId": envelope["eventId"]}

    return app


def _kafka_producer() -> Producer:
    from kafka import KafkaProducer

    return KafkaProducer(
        bootstrap_servers=os.environ.get("KAFKA_BROKERS", "localhost:19092").split(","),
        acks="all",
        linger_ms=2,
        retries=5,
    )


def _app_from_env() -> FastAPI:
    database_url = os.environ.get("DATABASE_URL")
    cache = SourceCache(load_http_sources(database_url)) if database_url else None
    return create_app(sources=cache, make_producer=_kafka_producer if database_url else None)


app = _app_from_env()
