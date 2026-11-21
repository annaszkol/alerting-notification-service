"""Throttling per person and channel (spec §6 step 4, §13 test 15)."""

import threading
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg.types.json import Jsonb

from contracts.delivery import (
    AlertForPlanning,
    NotificationKind,
    NotificationStatus,
    RuleForPlanning,
    Severity,
)
from db.testing import TEST_DATABASE_URL, temporary_database
from services.delivery.directory import SqlDirectory, StaticDirectory
from services.delivery.planning import NotificationPlanner
from services.delivery.schedule import DEFAULT_LIMITS, Limit, merge_limits, parse_window

T0 = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)

# --- limit resolution (pure) ---------------------------------------------------------------------------


def test_parse_window():
    assert parse_window("10m") == timedelta(minutes=10)
    assert parse_window("1h") == timedelta(hours=1)
    for bad in ("10", "m", "0m", "-5m", "10x"):
        with pytest.raises(ValueError):
            parse_window(bad)


def test_merge_limits_layers():
    team = {"sms": {"max": 3, "window": "5m"}, "email": None}  # team removes the email limit
    own = {"sms": {"max": 10, "window": "1h"}, "ui": {"max": 100, "window": "1m"}, "phone": {"max": "x"}}
    merged = merge_limits(team, own)
    assert merged["sms"] == Limit(10, timedelta(hours=1))  # the person wins
    assert "email" not in merged
    assert merged["phone"] == DEFAULT_LIMITS["phone"]  # invalid entry ignored
    assert merged["ui"] == Limit(100, timedelta(minutes=1))
    assert merge_limits() == DEFAULT_LIMITS


def test_profile_limits_precedence():
    directory = StaticDirectory(
        teams={"rates": {"anna": "member"}, "payments": {"anna": "member"}},
        team_limits={"rates": {"sms": {"max": 2, "window": "10m"}}, "payments": {"sms": {"max": 3, "window": "10m"}}},
        user_limits={},
    )
    assert directory.profiles(None, ["anna"], "payments")["anna"].limits["sms"].max == 3  # owner team first
    assert directory.profiles(None, ["anna"], None)["anna"].limits["sms"].max == 3  # then by team id
    assert directory.profiles(None, ["anna"], "rates")["anna"].limits["sms"].max == 2


# --- database ------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def database():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    with temporary_database("test_throttle", migrate=True) as conninfo:
        yield conninfo


@pytest.fixture
def db(database):
    with psycopg.connect(database, autocommit=True) as conn:
        conn.execute("TRUNCATE notifications, channel_preferences, team_members, teams, users CASCADE")
        conn.execute("INSERT INTO users (id, name) VALUES ('anna', 'Anna'), ('bob', 'Bob')")
        conn.execute("INSERT INTO teams (id, name) VALUES ('rates', 'Rates')")
        conn.execute("INSERT INTO team_members VALUES ('rates', 'anna', 'member'), ('rates', 'bob', 'manager')")
        conn.execute(
            "INSERT INTO channel_preferences (owner_type, owner_id, limits) VALUES ('team', 'rates', %s)",
            (Jsonb({"sms": {"max": 5, "window": "10m"}, "email": {"max": 2, "window": "10m"}}),),
        )
    return database


PLANNER = NotificationPlanner(SqlDirectory())


def rule(channels, to=None):
    return RuleForPlanning("r", {"team": "rates"}, [{"to": to or {"user": "anna"}, "channels": channels}])


def alert(n, severity=Severity.ERROR):
    return AlertForPlanning(id=n, rule_id="r", severity=severity, first_event_id=f"evt-{n}")


def plan_many(conninfo, count, channels=("sms",), start=0, at=T0, step=timedelta(seconds=10), severity=Severity.ERROR):
    """Plan `count` new alerts, each in its own committed transaction (like separate processor batches)."""
    results = []
    with psycopg.connect(conninfo) as conn:
        for i in range(start, start + count):
            result = PLANNER(conn, alert(i, severity), rule(list(channels)), at + step * (i - start))
            conn.commit()
            results.append(result)
    return results


def rows(conninfo, channel="sms", user="anna"):
    with psycopg.connect(conninfo) as conn:
        return conn.execute(
            "SELECT kind, status FROM notifications WHERE user_id = %s AND channel = %s ORDER BY id", (user, channel)
        ).fetchall()


@pytest.mark.db
def test_spec_test_15_exactly_one_limit_message(db):
    """Spec §13 test 15: over the SMS limit -> exactly one "limit reached", further alerts only in the UI."""
    plan_many(db, 9)

    assert rows(db) == [
        *[("alert", "pending")] * 5,
        ("limit", "pending"),
        ("alert", "held"),
        ("alert", "held"),
        ("alert", "held"),
        ("alert", "held"),
    ]


@pytest.mark.db
def test_plan_result_reports_limit_and_held(db):
    *_, sixth = plan_many(db, 6)
    assert [(n.kind, n.status) for n in sixth.notifications] == [
        (NotificationKind.LIMIT, NotificationStatus.PENDING),
        (NotificationKind.ALERT, NotificationStatus.HELD),
    ]


@pytest.mark.db
def test_window_slides(db):
    plan_many(db, 7, step=timedelta(seconds=1))  # 5 sent, limit, 2 held, all around T0
    later = T0 + timedelta(minutes=10, seconds=10)  # the first sends left the window
    plan_many(db, 6, start=100, at=later, step=timedelta(seconds=1))

    tail = rows(db)[-7:]
    assert tail == [*[("alert", "pending")] * 5, ("limit", "pending"), ("alert", "held")]
    assert sum(1 for kind, _ in rows(db) if kind == "limit") == 2  # one per window


@pytest.mark.db
def test_held_alerts_do_not_use_up_the_limit(db):
    """Held alerts were never sent, so they must not keep the person blocked."""
    plan_many(db, 20, step=timedelta(seconds=1))  # 5 sent + 15 held within a minute
    plan_many(db, 1, start=500, at=T0 + timedelta(minutes=10, seconds=30))
    assert rows(db)[-1] == ("alert", "pending")


@pytest.mark.db
def test_channels_are_limited_separately(db):
    plan_many(db, 3, channels=("sms", "email", "ui"))
    assert [s for _, s in rows(db, "sms")] == ["pending"] * 3
    assert rows(db, "email") == [("alert", "pending"), ("alert", "pending"), ("limit", "pending"), ("alert", "held")]
    assert [s for _, s in rows(db, "ui")] == ["pending"] * 3  # ui is not limited


@pytest.mark.db
def test_people_are_limited_separately(db):
    with psycopg.connect(db) as conn:
        for i in range(6):
            PLANNER(conn, alert(i), rule(["sms"], to={"team": "rates"}), T0 + timedelta(seconds=i))
        conn.commit()
    assert rows(db, user="anna")[-2:] == [("limit", "pending"), ("alert", "held")]
    assert rows(db, user="bob")[-2:] == [("limit", "pending"), ("alert", "held")]


@pytest.mark.db
def test_critical_phone_bypasses_the_limit(db):
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            "UPDATE channel_preferences SET limits = %s WHERE owner_id = 'rates'",
            (Jsonb({"phone": {"max": 1, "window": "10m"}}),),
        )
    plan_many(db, 4, channels=("phone",), severity=Severity.CRITICAL)
    assert [s for _, s in rows(db, "phone")] == ["pending"] * 4
    plan_many(db, 2, channels=("phone",), start=10, severity=Severity.ERROR)  # not critical: limited
    assert rows(db, "phone")[-3:] == [("limit", "pending"), ("alert", "held"), ("alert", "held")]


@pytest.mark.db
def test_replanning_the_same_event_changes_nothing(db):
    plan_many(db, 6)
    before = rows(db)
    plan_many(db, 6)  # same alerts / first events again (e.g. replay)
    assert rows(db) == before


@pytest.mark.db
def test_concurrent_processors_respect_the_limit_exactly(db):
    """Two processor transactions planning for the same person at once: the advisory lock makes the count exact."""
    barrier = threading.Barrier(2)
    errors = []

    def processor(offset):
        try:
            with psycopg.connect(db) as conn:
                barrier.wait()
                for i in range(offset, offset + 4):  # 4 alerts per batch, one transaction
                    PLANNER(conn, alert(i), rule(["sms"]), T0)
                conn.commit()
        except Exception as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=processor, args=(offset,)) for offset in (0, 100)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    statuses = rows(db)
    assert statuses.count(("alert", "pending")) == 5
    assert statuses.count(("limit", "pending")) == 1
    assert statuses.count(("alert", "held")) == 3
