"""Email over SMTP (Mailpit in the demo)."""

from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage
from email.utils import formatdate
from typing import Any

from contracts.delivery import Message, SendResult, SendStatus
from services.delivery.channels.registry import register


@register
class SmtpEmailAdapter:
    adapter = "smtp"

    def __init__(self, config: dict[str, Any]) -> None:
        self.host: str = config.get("host", "localhost")
        self.port = int(config.get("port", 25))
        self.sender: str = config.get("from", "alerts@alerting.local")
        self.timeout = float(config.get("timeout", 10))

    def build(self, message: Message) -> EmailMessage:
        email = EmailMessage()
        email["From"] = self.sender
        email["To"] = message.recipient.email
        email["Subject"] = message.subject
        email["Date"] = formatdate(localtime=False)
        # The notification id is the idempotency key: a retried send carries the same Message-ID.
        email["Message-ID"] = f"<notification-{message.notification_id}@alerting.local>"
        email["X-Alert-Id"] = str(message.alert_id)
        email.set_content(message.text)
        if message.html:
            email.add_alternative(message.html, subtype="html")
        return email

    async def send(self, message: Message) -> SendResult:
        if not message.recipient.email:
            error = f"user {message.recipient.user_id} has no email"
            return SendResult(SendStatus.PERMANENT_ERROR, error=error)
        try:
            await asyncio.to_thread(self._send_sync, self.build(message))
        except smtplib.SMTPRecipientsRefused as exc:
            return SendResult(SendStatus.PERMANENT_ERROR, error=f"recipient refused: {exc.recipients}")
        except smtplib.SMTPResponseException as exc:
            status = SendStatus.PERMANENT_ERROR if 500 <= exc.smtp_code < 600 else SendStatus.RETRYABLE_ERROR
            return SendResult(status, error=f"SMTP {exc.smtp_code}: {exc.smtp_error!r}")
        except (smtplib.SMTPException, OSError) as exc:  # disconnects, timeouts, refused connections
            return SendResult(SendStatus.RETRYABLE_ERROR, error=f"{type(exc).__name__}: {exc}")
        return SendResult(SendStatus.SENT, provider_message_id=f"notification-{message.notification_id}")

    def _send_sync(self, email: EmailMessage) -> None:
        with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as smtp:
            smtp.send_message(email)
