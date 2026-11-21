"""SMS and phone adapters for an HTTP gateway (the fake gateway in mocks/fake_gateway).

A real provider (e.g. Twilio) is a new class with its own `adapter` name and a row in `channels`.
"""

from __future__ import annotations

from typing import Any, ClassVar

import httpx

from contracts.delivery import Message, SendResult, SendStatus
from services.delivery.channels.registry import register


class HttpGatewayAdapter:
    adapter: ClassVar[str]

    def __init__(self, config: dict[str, Any], transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.url: str = config["url"]
        self.timeout = float(config.get("timeout", 5))
        self.client = httpx.AsyncClient(transport=transport, timeout=self.timeout)

    async def send(self, message: Message) -> SendResult:
        if not message.recipient.phone:
            error = f"user {message.recipient.user_id} has no phone"
            return SendResult(SendStatus.PERMANENT_ERROR, error=error)
        body = {
            "to": message.recipient.phone,
            "text": message.text,
            "idempotencyKey": str(message.notification_id),
            "replyCode": message.reply_code,
        }
        try:
            response = await self.client.post(self.url, json=body)
        except httpx.HTTPError as exc:  # timeouts, refused connections, ...
            return SendResult(SendStatus.RETRYABLE_ERROR, error=f"{type(exc).__name__}: {exc}")
        return classify(response)

    async def aclose(self) -> None:
        await self.client.aclose()


def classify(response: httpx.Response) -> SendResult:
    if response.is_success:
        try:
            provider_id = str(response.json().get("id"))
        except ValueError:
            provider_id = None
        return SendResult(SendStatus.SENT, provider_message_id=provider_id)
    error = f"HTTP {response.status_code}: {response.text[:200]}"
    if response.status_code in (408, 425, 429) or response.status_code >= 500:
        return SendResult(SendStatus.RETRYABLE_ERROR, error=error)
    return SendResult(SendStatus.PERMANENT_ERROR, error=error)


@register
class FakeSmsAdapter(HttpGatewayAdapter):
    adapter = "fake_sms"


@register
class FakePhoneAdapter(HttpGatewayAdapter):
    adapter = "fake_phone"
