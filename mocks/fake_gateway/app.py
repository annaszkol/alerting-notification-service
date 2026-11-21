"""Fake SMS / phone gateway (mock, owner B, spec §2, §6).

Outbound (called by the delivery channel adapters):
  POST /sms    {to, text, idempotencyKey, replyCode?} -> 202; 200 for a repeated key; 503 in outage
  POST /calls  {to, text, idempotencyKey, replyCode?} -> same

Operator / demo (the web page at / uses these):
  GET  /api/messages?channel=sms|phone&direction=out|in  -> recorded messages, with receivedAt (latency probe)
  DELETE /api/messages                                   -> clear
  GET|POST /api/outage {sms?: bool, phone?: bool}        -> "awaria" toggle
  POST /api/reply {from, text}                           -> inbound SMS, forwarded to the API
  POST /api/calls/{id}/press1                            -> "press 1" = ACK <replyCode> from the callee

Inbound replies go to INBOUND_SMS_URL (contracts/delivery.md). State is in memory: a restart clears it.
"""

from __future__ import annotations

import itertools
import os
import threading
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

INBOUND_SMS_URL = os.environ.get("INBOUND_SMS_URL", "http://api:8000/api/inbound/sms")
MAX_MESSAGES = 50_000

ChannelName = Literal["sms", "phone"]


class Outbound(BaseModel):
    to: str = Field(min_length=1)
    text: str
    idempotencyKey: str = Field(min_length=1)  # noqa: N815 - wire format
    replyCode: str | None = None  # noqa: N815


class Reply(BaseModel):
    from_: str = Field(alias="from", min_length=1)
    text: str = Field(min_length=1)


class OutageUpdate(BaseModel):
    sms: bool | None = None
    phone: bool | None = None


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Gateway:
    """In-memory store. Thread-safe because FastAPI runs sync endpoints in a thread pool."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.ids = itertools.count(1)
        self.messages: deque[dict[str, Any]] = deque(maxlen=MAX_MESSAGES)
        self.by_key: dict[tuple[str, str], dict[str, Any]] = {}
        self.outage: dict[str, bool] = {"sms": False, "phone": False}

    def accept(self, channel: ChannelName, body: Outbound) -> tuple[dict[str, Any], bool]:
        """Record an outbound message; returns (record, is_new)."""
        with self.lock:
            if self.outage[channel]:
                raise HTTPException(status_code=503, detail=f"{channel} gateway outage (simulated)")
            key = (channel, body.idempotencyKey)
            if key in self.by_key:
                record = self.by_key[key]
                record["duplicates"] += 1
                return record, False
            record = {
                "id": next(self.ids),
                "channel": channel,
                "direction": "out",
                "to": body.to,
                "text": body.text,
                "replyCode": body.replyCode,
                "idempotencyKey": body.idempotencyKey,
                "receivedAt": now_iso(),
                "duplicates": 0,
                "pressed1": None,
            }
            if len(self.messages) == self.messages.maxlen:
                oldest = self.messages[0]
                self.by_key.pop((oldest["channel"], oldest["idempotencyKey"]), None)
            self.messages.append(record)
            self.by_key[key] = record
            return record, True

    def record_inbound(self, sender: str, text: str) -> dict[str, Any]:
        with self.lock:
            record = {
                "id": next(self.ids),
                "channel": "sms",
                "direction": "in",
                "from": sender,
                "text": text,
                "receivedAt": now_iso(),
                "result": None,
            }
            self.messages.append(record)
            return record

    def find(self, message_id: int) -> dict[str, Any] | None:
        with self.lock:
            return next((m for m in self.messages if m["id"] == message_id), None)

    def list(self, channel: str | None, direction: str | None) -> list[dict[str, Any]]:
        with self.lock:
            return [
                dict(m)
                for m in self.messages
                if channel in (None, m["channel"]) and direction in (None, m["direction"])
            ]

    def clear(self) -> None:
        with self.lock:
            self.messages.clear()
            self.by_key.clear()


def create_app(
    inbound_url: str = INBOUND_SMS_URL, transport: httpx.AsyncBaseTransport | None = None
) -> FastAPI:
    app = FastAPI(title="fake-gateway")
    gateway = Gateway()
    app.state.gateway = gateway
    page = (Path(__file__).parent / "index.html").read_text(encoding="utf-8")

    async def forward(sender: str, text: str, record: dict[str, Any]) -> dict[str, Any]:
        """Send an inbound SMS to the API, store and return its answer."""
        body = {
            "from": sender,
            "text": text,
            "receivedAt": record["receivedAt"],
            "gatewayMessageId": str(record["id"]),
        }
        try:
            async with httpx.AsyncClient(transport=transport, timeout=5) as client:
                response = await client.post(inbound_url, json=body)
            if response.status_code == 200:
                result = response.json()
            else:
                result = {"result": f"http_{response.status_code}"}
        except (httpx.HTTPError, ValueError) as exc:
            result = {"result": "api_unreachable", "error": str(exc)}
        record["result"] = result.get("result")
        return result

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return page

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "service": "fake-gateway"}

    @app.post("/sms", status_code=202)
    def send_sms(body: Outbound, response: Response) -> dict[str, Any]:
        record, new = gateway.accept("sms", body)
        if not new:
            response.status_code = 200
        return {"id": record["id"], "duplicate": not new}

    @app.post("/calls", status_code=202)
    def place_call(body: Outbound, response: Response) -> dict[str, Any]:
        record, new = gateway.accept("phone", body)
        if not new:
            response.status_code = 200
        return {"id": record["id"], "duplicate": not new}

    @app.get("/api/messages")
    def messages(channel: ChannelName | None = None, direction: Literal["in", "out"] | None = None):
        return gateway.list(channel, direction)

    @app.delete("/api/messages", status_code=204)
    def clear() -> None:
        gateway.clear()

    @app.get("/api/outage")
    def get_outage() -> dict[str, bool]:
        return dict(gateway.outage)

    @app.post("/api/outage")
    def set_outage(update: OutageUpdate) -> dict[str, bool]:
        with gateway.lock:
            for channel, value in update.model_dump(exclude_none=True).items():
                gateway.outage[channel] = value
            return dict(gateway.outage)

    @app.post("/api/reply")
    async def reply(body: Reply) -> dict[str, Any]:
        record = gateway.record_inbound(body.from_, body.text)
        return await forward(body.from_, body.text, record)

    @app.post("/api/calls/{message_id}/press1")
    async def press1(message_id: int) -> dict[str, Any]:
        call = gateway.find(message_id)
        if call is None or call["channel"] != "phone":
            raise HTTPException(status_code=404, detail="call not found")
        if not call["replyCode"]:
            raise HTTPException(status_code=409, detail="call has no reply code")
        text = f"ACK {call['replyCode']}"
        record = gateway.record_inbound(call["to"], text)
        result = await forward(call["to"], text, record)
        call["pressed1"] = result.get("result")
        return result

    return app


app = create_app()
