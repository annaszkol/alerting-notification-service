"""Inbound SMS / phone "press 1" from the gateway (contracts/openapi.yaml `inboundSms`, contracts/delivery.md).

Thin route agreed in docs/plans/c-api-web.md: the logic is B's (`services.delivery.inbound`). No login: the gateway
is on the internal network, and every command is checked against the sender's phone number.
"""

from __future__ import annotations

from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from services.api.db import get_conn
from services.delivery.inbound import handle_inbound

router = APIRouter(tags=["inbound"])
Conn = Annotated[psycopg.Connection, Depends(get_conn)]


class InboundSms(BaseModel):
    from_: str = Field(alias="from", min_length=1, max_length=32)
    text: str = Field(max_length=480)
    receivedAt: str | None = None  # noqa: N815 - wire format
    gatewayMessageId: str | None = None  # noqa: N815


@router.post("/inbound/sms")
def inbound_sms(body: InboundSms, conn: Conn) -> dict[str, Any]:
    return {"result": handle_inbound(conn, body.from_, body.text)}
