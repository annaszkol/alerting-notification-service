import asyncio
import smtplib

import httpx
import pytest

from contracts.delivery import Channel, Message, NotificationKind, Recipient, SendStatus
from services.delivery.channels import registry
from services.delivery.channels.email import SmtpEmailAdapter
from services.delivery.channels.http_gateway import FakePhoneAdapter, FakeSmsAdapter
from services.delivery.messages import SMS_MAX, phone_text, sms_text

ANNA = Recipient(user_id="anna", name="Anna", email="anna@example.test", phone="+48500100200", timezone="UTC")


def message(channel=Channel.SMS, recipient=ANNA, **kw) -> Message:
    defaults = dict(
        notification_id=42,
        alert_id=7,
        kind=NotificationKind.ALERT,
        channel=channel,
        recipient=recipient,
        subject="Settlement failed: wf-1",
        text="[CRITICAL] Settlement failed: wf-1 http://localhost:3000/a/7 ACK K7Q2MX",
        reply_code="K7Q2MX",
    )
    return Message(**(defaults | kw))


def run(coro):
    return asyncio.run(coro)


# --- registry ------------------------------------------------------------------------------------------


def test_registry_knows_builtin_adapters():
    assert {"smtp", "fake_sms", "fake_phone", "websocket"} <= set(registry.names())
    assert isinstance(registry.create("fake_sms", {"url": "http://gw/sms"}), FakeSmsAdapter)


def test_registry_rejects_unknown_adapter():
    with pytest.raises(ValueError, match="unknown channel adapter"):
        registry.create("pigeon", {})


def test_ui_adapter_marks_sent():
    assert run(registry.create("websocket", {}).send(message(Channel.UI))).status is SendStatus.SENT


# --- HTTP gateway (SMS / phone) ------------------------------------------------------------------------


def gateway(handler, cls=FakeSmsAdapter):
    return cls({"url": "http://gw/sms"}, transport=httpx.MockTransport(handler))


def test_sms_sends_idempotency_key_and_code():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(202, json={"id": 5, "duplicate": False})

    result = run(gateway(handler).send(message()))
    assert result.status is SendStatus.SENT
    assert result.provider_message_id == "5"
    body = httpx.Response(200, content=seen[0].content).json()
    assert body == {
        "to": "+48500100200",
        "text": message().text,
        "idempotencyKey": "42",
        "replyCode": "K7Q2MX",
    }


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (200, SendStatus.SENT),
        (202, SendStatus.SENT),
        (429, SendStatus.RETRYABLE_ERROR),
        (500, SendStatus.RETRYABLE_ERROR),
        (503, SendStatus.RETRYABLE_ERROR),
        (400, SendStatus.PERMANENT_ERROR),
        (404, SendStatus.PERMANENT_ERROR),
        (422, SendStatus.PERMANENT_ERROR),
    ],
)
def test_http_status_mapping(status, expected):
    result = run(gateway(lambda r: httpx.Response(status, json={})).send(message()))
    assert result.status is expected
    if expected is not SendStatus.SENT:
        assert f"HTTP {status}" in result.error


@pytest.mark.parametrize("exc", [httpx.ConnectError("refused"), httpx.ReadTimeout("slow")])
def test_network_errors_are_retryable(exc):
    def handler(request):
        raise exc

    result = run(gateway(handler, FakePhoneAdapter).send(message(Channel.PHONE)))
    assert result.status is SendStatus.RETRYABLE_ERROR


def test_no_phone_is_permanent():
    no_phone = Recipient(user_id="bob", name="Bob", email=None, phone=None, timezone="UTC")
    result = run(gateway(lambda r: httpx.Response(202)).send(message(recipient=no_phone)))
    assert result.status is SendStatus.PERMANENT_ERROR


# --- SMTP ----------------------------------------------------------------------------------------------


class FakeSMTP:
    sent: list = []
    fail_with: Exception | None = None

    def __init__(self, host, port, timeout):
        self.address = (host, port)

    def __enter__(self):
        if FakeSMTP.fail_with:
            raise FakeSMTP.fail_with
        return self

    def __exit__(self, *exc):
        return False

    def send_message(self, email):
        FakeSMTP.sent.append(email)


@pytest.fixture
def smtp(monkeypatch):
    FakeSMTP.sent = []
    FakeSMTP.fail_with = None
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    return FakeSMTP


EMAIL = SmtpEmailAdapter({"host": "mailpit", "port": 1025, "from": "alerts@alerting.local"})


def test_email_is_built_with_stable_message_id(smtp):
    msg = message(Channel.EMAIL, text="Step settle: TIMEOUT", html="<p>Step settle: TIMEOUT</p>")
    result = run(EMAIL.send(msg))
    assert result.status is SendStatus.SENT
    [email] = smtp.sent
    assert email["To"] == "anna@example.test"
    assert email["Subject"] == "Settlement failed: wf-1"
    assert email["Message-ID"] == "<notification-42@alerting.local>"
    assert email.get_body(("plain",)).get_content().strip() == "Step settle: TIMEOUT"
    assert "<p>" in email.get_body(("html",)).get_content()


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ConnectionRefusedError("down"), SendStatus.RETRYABLE_ERROR),
        (smtplib.SMTPServerDisconnected("bye"), SendStatus.RETRYABLE_ERROR),
        (smtplib.SMTPResponseException(451, b"try later"), SendStatus.RETRYABLE_ERROR),
        (smtplib.SMTPResponseException(550, b"no such user"), SendStatus.PERMANENT_ERROR),
        (smtplib.SMTPRecipientsRefused({"x@y": (550, b"no")}), SendStatus.PERMANENT_ERROR),
    ],
)
def test_smtp_error_mapping(smtp, error, expected):
    smtp.fail_with = error
    assert run(EMAIL.send(message(Channel.EMAIL))).status is expected


def test_no_email_is_permanent(smtp):
    no_email = Recipient(user_id="bob", name="Bob", email=None, phone=None, timezone="UTC")
    assert run(EMAIL.send(message(Channel.EMAIL, recipient=no_email))).status is SendStatus.PERMANENT_ERROR
    assert smtp.sent == []


# --- message texts -------------------------------------------------------------------------------------


def test_sms_text_fits_and_keeps_link_and_code():
    text = sms_text("critical", "Settlement failed: wf-1", "http://localhost:3000/a/7", "K7Q2MX")
    assert text == "[CRITICAL] Settlement failed: wf-1 http://localhost:3000/a/7 ACK K7Q2MX"


def test_long_sms_title_is_truncated_to_160():
    text = sms_text("warning", "x" * 500, "http://localhost:3000/a/123456", "K7Q2MX")
    assert len(text) == SMS_MAX
    assert text.endswith("… http://localhost:3000/a/123456 ACK K7Q2MX")


def test_sms_text_collapses_whitespace_and_works_without_code():
    assert sms_text("info", "a\n  b", "/a/1", None) == "[INFO] a b /a/1"


def test_phone_text():
    assert phone_text("critical", "Settlement failed") == ("Critical alert. Settlement failed. Press 1 to acknowledge.")


# --- channels table ------------------------------------------------------------------------------------


@pytest.mark.db
def test_migrated_channels_use_registered_adapters():
    import psycopg

    from db.testing import TEST_DATABASE_URL, temporary_database

    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    with temporary_database("test_channels", migrate=True) as conninfo, psycopg.connect(conninfo) as conn:
        rows = conn.execute("SELECT name, adapter, config FROM channels ORDER BY name").fetchall()
    assert [r[0] for r in rows] == sorted(c.value for c in Channel)
    for _, adapter, config in rows:
        registry.create(adapter, config)  # raises for an unknown adapter or a missing config key
