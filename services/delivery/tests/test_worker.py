"""Delivery worker: claim with lease + SKIP LOCKED, retry with backoff, dead letters (spec §6)."""

import asyncio
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from contracts.delivery import Channel, NotificationKind, SendResult, SendStatus
from db.testing import TEST_DATABASE_URL, temporary_database
from services.delivery.messages import fill_live_fields
from services.delivery.worker import (
    CLAIM_SQL,
    LEASE,
    MAX_ATTEMPTS,
    Claimed,
    DeliveryWorker,
    backoff,
    claim,
    complete,
    decide,
    render,
)

T0 = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
BASE = "http://ui.test"

ANNA = {
    "id": "anna",
    "name": "Anna",
    "email": "anna@example.test",
    "phone": "+48500100200",
    "timezone": "UTC",
}
ALERT = {
    "id": 7,
    "title": "Settlement failed: wf-1 ({{$count}}x, {{$state}})",
    "body": "Step settle: TIMEOUT",
    "severity": "critical",
    "state": "open",
    "count": 3,
}


def claimed(channel=Channel.SMS, kind=NotificationKind.ALERT, code="K7Q2MX", attempts=1) -> Claimed:
    return Claimed(1, 7, "anna", channel, kind, "evt-1", attempts, code)


# --- pure functions ------------------------------------------------------------------------------------


def test_backoff_doubles_and_caps_at_5_minutes():
    assert [backoff(n).total_seconds() for n in range(1, 10)] == [5, 10, 20, 40, 80, 160, 300, 300, 300]


def test_decide():
    sent = decide(SendResult(SendStatus.SENT), 1, T0)
    assert (sent.status, sent.sent_at) == ("sent", T0)

    retry = decide(SendResult(SendStatus.RETRYABLE_ERROR, error="503"), 3, T0)
    assert (retry.status, retry.next_attempt_at) == ("pending", T0 + timedelta(seconds=20))
    assert retry.last_error == "503"

    exhausted = decide(SendResult(SendStatus.RETRYABLE_ERROR, error="503"), MAX_ATTEMPTS, T0)
    assert exhausted.status == "dead"
    assert "gave up" in exhausted.last_error

    permanent = decide(SendResult(SendStatus.PERMANENT_ERROR, error="no phone"), 1, T0)
    assert (permanent.status, permanent.last_error) == ("dead", "no phone")


def test_render_sms_with_live_fields():
    message = render(claimed(), ANNA, ALERT, BASE)
    assert message.text == "[CRITICAL] Settlement failed: wf-1 (3x, open) http://ui.test/a/7 ACK K7Q2MX"
    assert message.recipient.phone == "+48500100200"
    assert message.notification_id == 1
    assert message.links == {"alert": "http://ui.test/a/7"}


def test_render_email_has_body_link_and_ack():
    message = render(claimed(Channel.EMAIL), ANNA, ALERT, BASE)
    assert message.subject == "Settlement failed: wf-1 (3x, open)"
    assert message.text.startswith("Step settle: TIMEOUT\n\nOpen the alert: http://ui.test/a/7")
    assert message.text.endswith("ACK K7Q2MX")


def test_render_phone_and_limit():
    phone = render(claimed(Channel.PHONE), ANNA, ALERT, BASE)
    assert phone.text == "Critical alert. Settlement failed: wf-1 (3x, open). Press 1 to acknowledge."
    limit = render(claimed(Channel.SMS, NotificationKind.LIMIT, code=None), ANNA, ALERT, BASE)
    assert limit.text.startswith("Limit reached: further alerts only in the UI.")
    assert len(limit.text) <= 160


def test_fill_live_fields():
    alert = {"count": 500, "state": None}
    assert fill_live_fields("{{$count}} / {{ $state }} / {{$alertUrl}} / {{x}}", alert, "/a/1") == (
        "500 / — / /a/1 / {{x}}"
    )


# --- database ------------------------------------------------------------------------------------------


class FakeAdapter:
    """Returns scripted results (default: sent) and records every message."""

    adapter = "fake"

    def __init__(self, script=None):
        self.script = list(script or [])
        self.sent = []

    async def send(self, message):
        self.sent.append(message)
        return self.script.pop(0) if self.script else SendResult(SendStatus.SENT)


DOWN = SendResult(SendStatus.RETRYABLE_ERROR, error="HTTP 503: outage")


@pytest.fixture(scope="module")
def database():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    with temporary_database("test_worker", migrate=True) as conninfo:
        with psycopg.connect(conninfo, autocommit=True) as conn:
            # `alerts` belongs to A and is not migrated yet; minimal shape from spec §8.
            conn.execute(
                """CREATE TABLE IF NOT EXISTS alerts (
                       id bigint PRIMARY KEY, title text, body text,
                       severity text, state text, count integer)"""
            )
        yield conninfo


@pytest.fixture
def db(database):
    with psycopg.connect(database, autocommit=True) as conn:
        conn.execute("TRUNCATE notifications, team_members, teams, users CASCADE")
        conn.execute("DELETE FROM alerts")
        conn.execute(
            "INSERT INTO users (id, name, email, phone) VALUES"
            " ('anna', 'Anna', 'anna@example.test', '+48500100200'), ('bob', 'Bob', NULL, NULL)"
        )
        conn.execute("INSERT INTO alerts VALUES (7, %(title)s, %(body)s, %(severity)s, %(state)s, %(count)s)", ALERT)
    return database


def add(conninfo, user="anna", channel="sms", key=None, at=T0, status="pending", n=1):
    with psycopg.connect(conninfo, autocommit=True) as conn:
        for i in range(n):
            conn.execute(
                "INSERT INTO notifications (alert_id, user_id, channel, dedup_key, status, next_attempt_at)"
                " VALUES (7, %s, %s, %s, %s, %s)",
                (user, channel, key or f"evt-{channel}-{user}-{i}", status, at),
            )


def table(conninfo):
    with psycopg.connect(conninfo) as conn:
        return conn.execute(
            "SELECT id, status, attempts, next_attempt_at, lease_until, last_error, sent_at"
            " FROM notifications ORDER BY id"
        ).fetchall()


def run(conninfo, coro_fn):
    async def go():
        async with await psycopg.AsyncConnection.connect(conninfo, autocommit=True) as conn:
            return await coro_fn(conn)

    return asyncio.run(go())


@pytest.mark.db
def test_claims_only_due_rows_and_sets_lease(db):
    add(db, channel="sms", at=T0)
    add(db, channel="email", at=T0 + timedelta(seconds=30))  # not due yet
    add(db, channel="phone", status="sent")

    rows = run(db, lambda conn: claim(conn, T0))

    assert [(r.channel, r.attempts) for r in rows] == [(Channel.SMS, 1)]
    status = {r[0]: r for r in table(db)}
    assert status[rows[0].id][1:3] == ("sending", 1)
    assert status[rows[0].id][4] == T0 + LEASE


@pytest.mark.db
def test_skip_locked_concurrent_claims_are_disjoint(db):
    add(db, n=6)

    async def scenario(conn_a):
        async with await psycopg.AsyncConnection.connect(db, autocommit=True) as conn_b:
            async with conn_a.transaction():  # A holds its row locks until the end of the block
                cur = await conn_a.execute(CLAIM_SQL, {"now": T0, "lease": LEASE, "limit": 4})
                first = {r[0] for r in await cur.fetchall()}
                second = {r.id for r in await claim(conn_b, T0, limit=10)}
            return first, second

    first, second = run(db, scenario)
    assert len(first) == 4
    assert len(second) == 2
    assert first.isdisjoint(second)


@pytest.mark.db
def test_expired_lease_is_reclaimed(db):
    add(db)
    run(db, lambda conn: claim(conn, T0))  # worker "dies" here

    assert run(db, lambda conn: claim(conn, T0 + LEASE - timedelta(seconds=1))) == []
    [again] = run(db, lambda conn: claim(conn, T0 + LEASE + timedelta(seconds=1)))
    assert again.attempts == 2


@pytest.mark.db
def test_stale_worker_cannot_overwrite_result(db):
    add(db)
    [old] = run(db, lambda conn: claim(conn, T0))
    [new] = run(db, lambda conn: claim(conn, T0 + LEASE + timedelta(seconds=1)))

    applied_new = run(db, lambda conn: complete(conn, [(new, decide(SendResult(SendStatus.SENT), 2, T0))]))
    applied_old = run(db, lambda conn: complete(conn, [(old, decide(DOWN, 1, T0))]))

    assert (applied_new, applied_old) == (1, 0)
    assert table(db)[0][1] == "sent"


@pytest.mark.db
def test_run_once_sends_and_records(db):
    add(db, channel="sms")
    add(db, channel="email")
    sms, email = FakeAdapter(), FakeAdapter()

    count = run(db, lambda conn: DeliveryWorker(conn, BASE, {"sms": sms, "email": email}).run_once(T0))

    assert count == 2
    assert [m.text for m in sms.sent] == [
        "[CRITICAL] Settlement failed: wf-1 (3x, open) http://ui.test/a/7"  # no reply code yet
    ]
    assert email.sent[0].recipient.email == "anna@example.test"
    assert [(r[1], r[5], r[6]) for r in table(db)] == [("sent", None, T0), ("sent", None, T0)]


@pytest.mark.db
def test_retry_with_backoff_then_success(db):
    add(db)
    adapter = FakeAdapter([DOWN])

    def worker(conn):
        return DeliveryWorker(conn, BASE, {"sms": adapter})

    run(db, lambda conn: worker(conn).run_once(T0))
    [(_, status, attempts, next_at, lease, error, _)] = table(db)
    assert (status, attempts, lease, error) == ("pending", 1, None, DOWN.error)
    assert next_at == T0 + timedelta(seconds=5)

    assert run(db, lambda conn: worker(conn).run_once(T0 + timedelta(seconds=4))) == 0
    assert run(db, lambda conn: worker(conn).run_once(T0 + timedelta(seconds=5))) == 1
    assert table(db)[0][1:3] == ("sent", 2)


@pytest.mark.db
def test_two_minute_outage_loses_nothing(db):
    """Spec §13 test 10 in miniature: gateway down for 2 minutes, then everything is delivered, 0 dead."""
    add(db, n=20)
    outage_until = T0 + timedelta(minutes=2)

    class Gateway(FakeAdapter):
        now = T0

        async def send(self, message):
            if self.now < outage_until:
                return DOWN
            return await super().send(message)

    gateway = Gateway()

    async def simulate(conn):
        worker = DeliveryWorker(conn, BASE, {"sms": gateway})
        for second in range(0, 10 * 60):  # 10 simulated minutes, one tick per second
            gateway.now = T0 + timedelta(seconds=second)
            await worker.run_once(gateway.now)

    run(db, simulate)

    statuses = [r[1] for r in table(db)]
    assert statuses == ["sent"] * 20
    assert len({m.notification_id for m in gateway.sent}) == 20


@pytest.mark.db
def test_gives_up_after_max_attempts(db):
    add(db)
    adapter = FakeAdapter([DOWN] * MAX_ATTEMPTS)

    async def simulate(conn):
        worker = DeliveryWorker(conn, BASE, {"sms": adapter})
        now = T0
        for _ in range(MAX_ATTEMPTS):
            assert await worker.run_once(now) == 1
            now += backoff(MAX_ATTEMPTS)  # always past the next attempt

    run(db, simulate)
    [(_, status, attempts, _, _, error, _)] = table(db)
    assert (status, attempts) == ("dead", MAX_ATTEMPTS)
    assert error.startswith("gave up")


@pytest.mark.db
def test_permanent_errors_are_dead_at_once(db):
    add(db, user="bob")  # bob has no phone
    add(db, user="anna", channel="email")

    from services.delivery.channels.http_gateway import FakeSmsAdapter

    bounced = FakeAdapter([SendResult(SendStatus.PERMANENT_ERROR, error="550 no such user")])
    sms = FakeSmsAdapter({"url": "http://gw.invalid/sms"})
    run(db, lambda conn: DeliveryWorker(conn, BASE, {"sms": sms, "email": bounced}).run_once(T0))

    assert [(r[1], r[2], r[5]) for r in table(db)] == [
        ("dead", 1, "user bob has no phone"),
        ("dead", 1, "550 no such user"),
    ]


@pytest.mark.db
def test_disabled_channel_keeps_the_row(db):
    add(db, channel="phone")
    run(db, lambda conn: DeliveryWorker(conn, BASE, {}).run_once(T0))
    [(_, status, _, _, _, error, _)] = table(db)
    assert status == "pending"
    assert "disabled" in error


@pytest.mark.db
def test_adapter_crash_is_retried(db):
    add(db)

    class Broken(FakeAdapter):
        async def send(self, message):
            raise RuntimeError("bug")

    run(db, lambda conn: DeliveryWorker(conn, BASE, {"sms": Broken()}).run_once(T0))
    assert table(db)[0][1] == "pending"


@pytest.mark.db
def test_load_adapters_from_channels_table(db):
    async def load(conn):
        worker = DeliveryWorker(conn, BASE)
        await worker.load_adapters()
        await worker.load_adapters()  # reload closes the old HTTP clients
        return {name: type(a).__name__ for name, a in worker.adapters.items()}

    assert run(db, load) == {
        "ui": "UiAdapter",
        "email": "SmtpEmailAdapter",
        "sms": "FakeSmsAdapter",
        "phone": "FakePhoneAdapter",
    }
