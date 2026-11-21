"""plan_notifications against a real Postgres (TEST_DATABASE_URL). Pure routing tests run without it."""

from datetime import UTC, datetime

import psycopg
import pytest

from contracts.delivery import (
    AlertForPlanning,
    Channel,
    NotificationKind,
    NotificationStatus,
    PlanNotifications,
    RuleForPlanning,
    Severity,
)
from db.testing import TEST_DATABASE_URL, temporary_database
from services.delivery.planning import NotificationPlanner, StaticDirectory, plan_targets

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)

DIRECTORY = StaticDirectory(
    {
        "rates": {"anna": "member", "bob": "manager"},
        "settlements": {"carol": "manager", "dave": "member"},
        "payments": {"anna": "member", "erin": "manager"},
    }
)
PLAN = NotificationPlanner(DIRECTORY)


def alert(alert_id: int = 1, first_event_id: str = "evt-1") -> AlertForPlanning:
    return AlertForPlanning(
        id=alert_id, rule_id="r", severity=Severity.CRITICAL, first_event_id=first_event_id
    )


def rule(route, rule_id: str = "r") -> RuleForPlanning:
    return RuleForPlanning(id=rule_id, owner={"team": "rates"}, route=route)


def test_planner_matches_contract():
    planner: PlanNotifications = PLAN  # static check that the signature fits the contract
    assert callable(planner)


# --- routing (no database) -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("route", "expected"),
    [
        ([], []),
        ([{"to": {"user": "anna"}, "channels": ["sms", "email"]}], [("anna", "email"), ("anna", "sms")]),
        ([{"to": {"team": "rates"}, "channels": ["email"]}], [("anna", "email"), ("bob", "email")]),
        ([{"to": {"team": "rates", "role": "manager"}, "channels": ["sms"]}], [("bob", "sms")]),
        # stub: on-call = whole team
        ([{"to": {"oncall": "settlements"}, "channels": ["phone"]}], [("carol", "phone"), ("dave", "phone")]),
        # stub: preferences / omitted / schedule -> ui
        ([{"to": {"user": "anna"}, "channels": "preferences"}], [("anna", "ui")]),
        ([{"to": {"user": "anna"}}], [("anna", "ui")]),
        (
            [{"to": {"user": "anna"}, "channels": {"schedule": [{"between": "08:00-20:00", "use": ["sms"]}],
                                                   "otherwise": ["email"]}}],
            [("anna", "ui")],
        ),
        ([{"to": {"team": "unknown"}, "channels": ["sms"]}], []),
    ],
)  # fmt: skip
def test_plan_targets(route, expected):
    assert plan_targets(None, DIRECTORY, route, NOW) == [(u, Channel(c)) for u, c in expected]


def test_person_in_several_route_entries_is_planned_once():
    route = [
        {"to": {"team": "rates"}, "channels": ["sms"]},
        {"to": {"user": "anna"}, "channels": ["sms"]},
        {"to": {"team": "payments"}, "channels": ["sms"]},
    ]
    assert plan_targets(None, DIRECTORY, route, NOW) == [
        ("anna", Channel.SMS),
        ("bob", Channel.SMS),
        ("erin", Channel.SMS),
    ]


# --- outbox rows (database) ----------------------------------------------------------------------------


@pytest.fixture(scope="module")
def database():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    with temporary_database("test_delivery", migrate=True) as conninfo:
        yield conninfo


@pytest.fixture
def conn(database):
    with psycopg.connect(database) as conn:
        conn.execute("TRUNCATE notifications")
        conn.commit()
        yield conn


def rows(conn):
    return conn.execute(
        "SELECT alert_id, user_id, channel, kind, dedup_key, status FROM notifications ORDER BY id"
    ).fetchall()


@pytest.mark.db
def test_creates_pending_rows_in_callers_transaction(conn):
    result = PLAN(conn, alert(), rule([{"to": {"team": "rates"}, "channels": ["email"]}]), NOW)

    assert [(n.user_id, n.channel, n.kind, n.status) for n in result.notifications] == [
        ("anna", Channel.EMAIL, NotificationKind.ALERT, NotificationStatus.PENDING),
        ("bob", Channel.EMAIL, NotificationKind.ALERT, NotificationStatus.PENDING),
    ]
    assert rows(conn) == [
        (1, "anna", "email", "alert", "evt-1", "pending"),
        (1, "bob", "email", "alert", "evt-1", "pending"),
    ]
    next_attempt = conn.execute("SELECT DISTINCT next_attempt_at FROM notifications").fetchall()
    assert next_attempt == [(NOW,)]

    conn.rollback()  # the planner never commits: rolling back the caller's transaction removes everything
    assert rows(conn) == []


@pytest.mark.db
def test_two_rules_same_event_one_message_per_person(conn):
    """Spec §13 test 14: a person in two teams, an event matching both teams' rules -> one SMS."""
    rates = rule([{"to": {"team": "rates"}, "channels": ["sms"]}], rule_id="rates-rule")
    payments = rule([{"to": {"team": "payments"}, "channels": ["sms"]}], rule_id="payments-rule")

    first = PLAN(conn, alert(alert_id=1, first_event_id="evt-9"), rates, NOW)
    second = PLAN(conn, alert(alert_id=2, first_event_id="evt-9"), payments, NOW)
    conn.commit()

    assert {n.user_id for n in first.notifications} == {"anna", "bob"}
    assert {n.user_id for n in second.notifications} == {"erin"}  # anna already has this event's SMS
    sms_to_anna = [r for r in rows(conn) if r[1] == "anna"]
    assert sms_to_anna == [(1, "anna", "sms", "alert", "evt-9", "pending")]


@pytest.mark.db
def test_replanning_is_idempotent(conn):
    route = rule([{"to": {"user": "anna"}, "channels": ["sms", "email"]}])
    PLAN(conn, alert(), route, NOW)
    again = PLAN(conn, alert(), route, NOW)
    conn.commit()

    assert again.notifications == []
    assert len(rows(conn)) == 2


@pytest.mark.db
def test_new_event_new_notifications(conn):
    """A new alert (different first event) for the same person is a new message."""
    route = rule([{"to": {"user": "anna"}, "channels": ["sms"]}])
    PLAN(conn, alert(alert_id=1, first_event_id="evt-1"), route, NOW)
    PLAN(conn, alert(alert_id=2, first_event_id="evt-2"), route, NOW)
    conn.commit()

    assert [(r[0], r[4]) for r in rows(conn)] == [(1, "evt-1"), (2, "evt-2")]


@pytest.mark.db
def test_ui_only_route_creates_nothing(conn):
    result = PLAN(conn, alert(), rule([]), NOW)
    assert result.notifications == []
    assert rows(conn) == []
