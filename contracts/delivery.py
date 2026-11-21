"""Delivery contracts (owner B, spec §6): plan_notifications and the channel adapter interface.

Implementations live in services/delivery/. Other components depend only on the names in this file.
Description and rules: contracts/delivery.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

import psycopg

# Shared enums ------------------------------------------------------------------------------------------


class Channel(StrEnum):
    UI = "ui"
    EMAIL = "email"
    SMS = "sms"
    PHONE = "phone"


class Severity(StrEnum):
    """Ordered: info < warning < error < critical (spec §3)."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class NotificationKind(StrEnum):
    ALERT = "alert"
    LIMIT = "limit"  # "limit reached, further alerts only in the UI"
    APPROVAL = "approval"  # request to approve a runbook step


class NotificationStatus(StrEnum):
    PENDING = "pending"
    SENDING = "sending"
    SENT = "sent"
    DEAD = "dead"
    HELD = "held"  # over the throttling limit; never sent, visible in the UI


# plan_notifications ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class AlertForPlanning:
    """The new alert, as the processor just inserted it (same transaction)."""

    id: int
    rule_id: str
    severity: Severity
    first_event_id: str  # dedup_key of kind=alert notifications (spec §6 step 3)


@dataclass(frozen=True)
class RuleForPlanning:
    id: str
    owner: dict[str, str]  # {"team": "rates"} or {"user": "anna"}
    route: list[dict[str, Any]]  # validated against contracts/route.schema.json


@dataclass(frozen=True)
class PlannedNotification:
    id: int
    user_id: str
    channel: Channel
    kind: NotificationKind
    status: NotificationStatus  # pending or held


@dataclass(frozen=True)
class PlanResult:
    notifications: list[PlannedNotification] = field(default_factory=list)


class PlanNotifications(Protocol):
    def __call__(
        self,
        conn: psycopg.Connection,
        alert: AlertForPlanning,
        rule: RuleForPlanning,
        now: datetime,
    ) -> PlanResult:
        """Insert notification rows for a NEW alert inside the caller's open transaction.

        Called by the processor only when `INSERT INTO alerts ... RETURNING id` returned a row.
        Must not commit, roll back, open another connection or do network I/O.
        Takes pg_advisory_xact_lock per (user, channel) for throttling; locks are released at COMMIT.
        Idempotent: re-running for the same alert creates nothing new (unique (user_id, channel, dedup_key)).
        """
        ...


# Channel adapter ---------------------------------------------------------------------------------------


class SendStatus(StrEnum):
    SENT = "sent"
    RETRYABLE_ERROR = "retryable_error"  # back to pending with backoff
    PERMANENT_ERROR = "permanent_error"  # dead


@dataclass(frozen=True)
class Recipient:
    user_id: str
    name: str
    email: str | None
    phone: str | None
    timezone: str


@dataclass(frozen=True)
class Message:
    """Rendered at send time, so live fields ({{$count}}, {{$state}}) are current."""

    notification_id: int  # idempotency key passed to the provider
    alert_id: int
    kind: NotificationKind
    channel: Channel
    recipient: Recipient
    subject: str  # title; for SMS/phone also the start of `text`
    text: str  # plain text; SMS <= 160 chars
    html: str | None = None  # email only
    reply_code: str | None = None  # code for ACK / APPROVE / REJECT replies
    links: dict[str, str] = field(default_factory=dict)  # "alert", "approve", "reject"


@dataclass(frozen=True)
class SendResult:
    status: SendStatus
    provider_message_id: str | None = None
    error: str | None = None  # stored in notifications.last_error


class ChannelAdapter(Protocol):
    """One class per provider; registered by `adapter` name and configured from the `channels` table."""

    adapter: str  # e.g. "smtp", "fake_sms", "fake_phone", "websocket"

    def __init__(self, config: dict[str, Any]) -> None: ...

    async def send(self, message: Message) -> SendResult:
        """Deliver one message. Never raises for provider errors: map them to SendStatus.

        Timeouts and 5xx -> RETRYABLE_ERROR; invalid address / 4xx -> PERMANENT_ERROR.
        Must pass message.notification_id to the provider as the idempotency key.
        """
        ...
