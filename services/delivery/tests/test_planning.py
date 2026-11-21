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
from services.delivery.directory import SqlDirectory, StaticDirectory
from services.delivery.planning import NotificationPlanner, plan_targets

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)

DIRECTORY = StaticDirectory(
    {
        "rates": {"anna": "member", "bob": "manager"},
        "settlements": {"carol": "manager", "dave": "member"},
        "payments": {"anna": "member", "erin": "manager"},
    }
)
PLAN = NotificationPlanner(DIRECTORY)  # in-memory, for tests without a database


def alert(alert_id: int = 1, first_event_id: str = "evt-1") -> AlertForPlanning:
    return AlertForPlanning(id=alert_id, rule_id="r", severity=Severity.CRITICAL, first_event_id=first_event_id)


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
        # no rotation for settlements in DIRECTORY: on-call falls back to the whole team
        ([{"to": {"oncall": "settlements"}, "channels": ["phone"]}], [("carol", "phone"), ("dave", "phone")]),
        # no preferences anywhere: built-in defaults, critical -> sms + email
        ([{"to": {"user": "anna"}, "channels": "preferences"}], [("anna", "email"), ("anna", "sms")]),
        ([{"to": {"user": "anna"}}], [("anna", "email"), ("anna", "sms")]),
        # schedule from the rule; NOW is 12:00 UTC and anna's zone defaults to UTC
        (
            [{"to": {"user": "anna"}, "channels": {"schedule": [{"between": "08:00-20:00", "use": ["sms"]}],
                                                   "otherwise": ["email"]}}],
            [("anna", "sms")],
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
    """Users and teams from DIRECTORY in the database; empty outbox."""
    with psycopg.connect(database) as conn:
        conn.execute("TRUNCATE notifications, team_members, teams, users CASCADE")
        users = sorted({u for members in DIRECTORY.teams.values() for u in members} | {"frank"})
        for user in users:
            conn.execute("INSERT INTO users (id, name) VALUES (%s, %s)", (user, user.title()))
        for team, members in DIRECTORY.teams.items():
            conn.execute("INSERT INTO teams (id, name) VALUES (%s, %s)", (team, team.title()))
            for user, role in members.items():
                conn.execute(
                    "INSERT INTO team_members (team_id, user_id, role) VALUES (%s, %s, %s)",
                    (team, user, role),
                )
        conn.commit()
        yield conn


SQL_PLAN = NotificationPlanner(SqlDirectory())


def rows(conn):
    return conn.execute(
        "SELECT alert_id, user_id, channel, kind, dedup_key, status FROM notifications ORDER BY id"
    ).fetchall()


@pytest.mark.db
def test_creates_pending_rows_in_callers_transaction(conn):
    result = SQL_PLAN(conn, alert(), rule([{"to": {"team": "rates"}, "channels": ["email"]}]), NOW)

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

    first = SQL_PLAN(conn, alert(alert_id=1, first_event_id="evt-9"), rates, NOW)
    second = SQL_PLAN(conn, alert(alert_id=2, first_event_id="evt-9"), payments, NOW)
    conn.commit()

    assert {n.user_id for n in first.notifications} == {"anna", "bob"}
    assert {n.user_id for n in second.notifications} == {"erin"}  # anna already has this event's SMS
    sms_to_anna = [r for r in rows(conn) if r[1] == "anna"]
    assert sms_to_anna == [(1, "anna", "sms", "alert", "evt-9", "pending")]


@pytest.mark.db
def test_replanning_is_idempotent(conn):
    route = rule([{"to": {"user": "anna"}, "channels": ["sms", "email"]}])
    SQL_PLAN(conn, alert(), route, NOW)
    again = SQL_PLAN(conn, alert(), route, NOW)
    conn.commit()

    assert again.notifications == []
    assert len(rows(conn)) == 2


@pytest.mark.db
def test_new_event_new_notifications(conn):
    """A new alert (different first event) for the same person is a new message."""
    route = rule([{"to": {"user": "anna"}, "channels": ["sms"]}])
    SQL_PLAN(conn, alert(alert_id=1, first_event_id="evt-1"), route, NOW)
    SQL_PLAN(conn, alert(alert_id=2, first_event_id="evt-2"), route, NOW)
    conn.commit()

    assert [(r[0], r[4]) for r in rows(conn)] == [(1, "evt-1"), (2, "evt-2")]


@pytest.mark.db
def test_ui_only_route_creates_nothing(conn):
    result = SQL_PLAN(conn, alert(), rule([]), NOW)
    assert result.notifications == []
    assert rows(conn) == []


@pytest.mark.db
def test_sql_directory_matches_static_directory(conn):
    sql = SqlDirectory()
    for team in DIRECTORY.teams:
        for role in (None, "manager", "member"):
            assert sql.team_members(conn, team, role) == DIRECTORY.team_members(None, team, role)
    assert sql.team_members(conn, "unknown") == []
    assert sql.existing_users(conn, ["anna", "ghost", "frank", "anna"]) == ["anna", "frank"]


@pytest.mark.db
def test_unknown_user_or_team_does_not_abort_the_transaction(conn):
    route = [
        {"to": {"user": "ghost"}, "channels": ["sms"]},
        {"to": {"team": "nobody"}, "channels": ["sms"]},
        {"to": {"user": "frank"}, "channels": ["sms"]},  # in no team, still a valid recipient
    ]
    result = SQL_PLAN(conn, alert(), rule(route), NOW)
    conn.commit()
    assert [(n.user_id, n.channel) for n in result.notifications] == [("frank", Channel.SMS)]


@pytest.mark.db
def test_notification_requires_existing_user(conn):
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        conn.execute(
            "INSERT INTO notifications (alert_id, user_id, channel, dedup_key) VALUES (1, 'ghost', 'sms', 'e')"
        )


@pytest.mark.db
def test_ack_codes_one_per_alert_and_person(conn):
    """contracts/delivery.md: one `ACK <code>` per (alert, person), shared by that person's channels."""
    from services.delivery.codes import CODE_ALPHABET, CODE_LENGTH

    SQL_PLAN(conn, alert(1), rule([{"to": {"team": "rates"}, "channels": ["sms", "email"]}]), NOW)
    codes = dict(conn.execute("SELECT user_id || ':' || channel, reply_code FROM notifications").fetchall())
    assert codes["anna:sms"] == codes["anna:email"]
    assert codes["bob:sms"] == codes["bob:email"]
    assert codes["anna:sms"] != codes["bob:sms"]
    assert all(len(c) == CODE_LENGTH and set(c) <= set(CODE_ALPHABET) for c in codes.values())

    # another rule adds a channel for the same alert: anna keeps her code
    SQL_PLAN(conn, alert(1, first_event_id="evt-1b"), rule([{"to": {"user": "anna"}, "channels": ["phone"]}]), NOW)
    phone = conn.execute("SELECT reply_code FROM notifications WHERE channel = 'phone'").fetchone()[0]
    assert phone == codes["anna:sms"]

    # a new alert gets a new code
    SQL_PLAN(conn, alert(2, first_event_id="evt-2"), rule([{"to": {"user": "anna"}, "channels": ["sms"]}]), NOW)
    new = conn.execute("SELECT reply_code FROM notifications WHERE alert_id = 2").fetchone()[0]
    assert new != codes["anna:sms"]


@pytest.mark.db
def test_planner_works_on_dict_row_connections(database):
    """The API's pool returns dict rows (rule tester, escalate from the API process); B's code must not care."""
    from psycopg.rows import dict_row

    with psycopg.connect(database, row_factory=dict_row) as c:
        result = SQL_PLAN(c, alert(9, first_event_id="evt-9"), rule([{"to": {"team": "rates"}}]), NOW)
        assert {n.user_id for n in result.notifications} == {"anna", "bob"}
        assert c.row_factory is dict_row  # restored for the caller
        c.rollback()
