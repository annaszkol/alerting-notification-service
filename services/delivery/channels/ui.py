"""UI channel. The browser gets alerts over WebSocket from the API (owner C, `LISTEN alerts_changed`),
so there is nothing to send here: the notification is marked sent and shown in the alert's timeline."""

from __future__ import annotations

from typing import Any

from contracts.delivery import Message, SendResult, SendStatus
from services.delivery.channels.registry import register


@register
class UiAdapter:
    adapter = "websocket"

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    async def send(self, message: Message) -> SendResult:
        return SendResult(SendStatus.SENT)
