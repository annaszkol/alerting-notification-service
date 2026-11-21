"""Channel schedules, preferences and on-call rotations (spec §4 Routing, §6 Dyżury)."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import psycopg
import pytest
from psycopg.types.json import Jsonb

from contracts.delivery import AlertForPlanning, Channel, RuleForPlanning, Severity
from db.testing import TEST_DATABASE_URL, temporary_database
from services.delivery.directory import SqlDirectory, StaticDirectory
from services.delivery.planning import NotificationPlanner, plan_targets
from services.delivery.schedule import DEFAULT_PREFERENCES, Rotation, in_window, pick_channels, preference_for

WARSAW = ZoneInfo("Europe/Warsaw")
NEW_YORK = ZoneInfo("America/New_York")


def local(y, m, d, hh, mm=0, tz=WARSAW):
    return datetime(y, m, d, hh, mm, tzinfo=tz)


# --- windows -------------------------------------------------------------------------------------------

FRI = (2026, 11, 20)  # Friday
SAT = (2026, 11, 21)  # Saturday


@pytest.mark.parametrize(
    ("between", "days", "when", "expected"),
    [
        ("08:00-20:00", None, local(*FRI, 8, 0), True),
        ("08:00-20:00", None, local(*FRI, 19, 59), True),
        ("08:00-20:00", None, local(*FRI, 20, 0), False),  # end exclusive
        ("08:00-20:00", None, local(*FRI, 7, 59), False),
        ("08:00-20:00", [1, 2, 3, 4, 5], local(*SAT, 12, 0), False),  # weekend
        ("08:00-20:00", [1, 2, 3, 4, 5], local(*FRI, 12, 0), True),
        ("22:00-06:00", None, local(*FRI, 23, 30), True),  # wraps midnight
        ("22:00-06:00", None, local(*SAT, 5, 59), True),
        ("22:00-06:00", None, local(*SAT, 6, 0), False),
        ("22:00-06:00", None, local(*SAT, 21, 59), False),
        ("22:00-06:00", [5], local(*SAT, 3, 0), True),  # Friday's night shift, seen on Saturday morning
        ("22:00-06:00", [5], local(*SAT, 23, 0), False),  # Saturday evening: not a Friday window
        ("00:00-00:00", None, local(*SAT, 13, 0), True),  # whole day
    ],
)
def test_in_window(between, days, when, expected):
    assert in_window(between, days, when) is expected


def test_pick_channels():
    schedule = {
        "schedule": [
            {"between": "08:00-20:00", "use": ["sms"]},
            {"between": "06:00-23:00", "use": ["email"]},
        ],
        "otherwise": ["phone"],
    }
    assert pick_channels(schedule, local(*FRI, 12)) == [Channel.SMS]  # first matching window wins
    assert pick_channels(schedule, local(*FRI, 21)) == [Channel.EMAIL]
    assert pick_channels(schedule, local(*FRI, 2)) == [Channel.PHONE]
    assert pick_channels({"schedule": schedule["schedule"], "otherwise": []}, local(*FRI, 2)) == []
    assert pick_channels(["ui", "email"], local(*FRI, 2)) == [Channel.UI, Channel.EMAIL]
    assert pick_channels(None, local(*FRI, 2)) == []


def test_preference_for_falls_back_to_lower_level():
    prefs = {"info": ["ui"], "error": ["email"]}
    assert preference_for(prefs, "info") == ["ui"]
    assert preference_for(prefs, "warning") == ["ui"]
    assert preference_for(prefs, "error") == ["email"]
    assert preference_for(prefs, "critical") == ["email"]
    assert preference_for({"critical": ["phone"]}, "error") is None


# --- rotations -----------------------------------------------------------------------------------------

DAILY = Rotation(
    "settlements", ("carol", "dave", "erin"), timedelta(days=1), time(9, 0), "Europe/Warsaw", date(2026, 11, 2)
)


@pytest.mark.parametrize(
    ("when", "expected"),
    [
        (local(2026, 11, 2, 9, 0), "carol"),
        (local(2026, 11, 3, 8, 59), "carol"),  # handoff at 09:00 local
        (local(2026, 11, 3, 9, 0), "dave"),
        (local(2026, 11, 4, 12, 0), "erin"),
        (local(2026, 11, 5, 12, 0), "carol"),  # cycles
        (local(2026, 11, 1, 12, 0), "erin"),  # before the start: counted backwards
        (datetime(2026, 11, 3, 8, 30, tzinfo=UTC), "dave"),  # 09:30 in Warsaw
    ],
)
def test_daily_rotation(when, expected):
    assert DAILY.on_call(when) == expected


def test_rotation_keeps_handoff_hour_across_dst():
    rotation = Rotation("t", ("a", "b"), timedelta(days=1), time(9, 0), "Europe/Warsaw", date(2026, 3, 27))
    # DST starts on 2026-03-29 in Warsaw; handoffs stay at 09:00 local time.
    assert rotation.on_call(local(2026, 3, 29, 8, 59)) == "b"
    assert rotation.on_call(local(2026, 3, 29, 9, 0)) == "a"
    assert rotation.on_call(local(2026, 3, 30, 9, 0)) == "b"


def test_weekly_rotation():
    weekly = Rotation("t", ("a", "b"), timedelta(weeks=1), time(10, 0), "UTC", date(2026, 11, 2))
    assert weekly.on_call(datetime(2026, 11, 8, 23, 0, tzinfo=UTC)) == "a"
    assert weekly.on_call(datetime(2026, 11, 9, 10, 0, tzinfo=UTC)) == "b"


# --- routing with zones and preferences (in memory) ----------------------------------------------------

DIRECTORY = StaticDirectory(
    teams={
        "payments": {"anna": "member", "erin": "manager"},
        "rates": {"anna": "member", "bob": "manager"},
        "settlements": {"carol": "manager", "dave": "member", "erin": "member"},
    },
    extra_users=frozenset({"frank"}),
    rotations={"settlements": DAILY},
    timezones={"anna": "America/New_York", "bob": "Europe/Warsaw"},
    user_preferences={"bob": {"info": ["ui"], "warning": ["email"]}},
    team_preferences={"payments": {"info": ["ui"], "critical": ["phone"]}, "rates": {"info": ["email"]}},
)

ANNA_SCHEDULE = [
    {
        "to": {"user": "anna"},
        "channels": {"schedule": [{"between": "08:00-20:00", "use": ["sms"]}], "otherwise": ["email"]},
    }
]


@pytest.mark.parametrize(
    ("utc", "expected"),
    [
        (datetime(2026, 11, 21, 13, 0, tzinfo=UTC), Channel.SMS),  # 08:00 in New York
        (datetime(2026, 11, 21, 12, 59, tzinfo=UTC), Channel.EMAIL),  # 07:59
        (datetime(2026, 11, 22, 0, 59, tzinfo=UTC), Channel.SMS),  # 19:59 on the 21st
        (datetime(2026, 11, 22, 1, 0, tzinfo=UTC), Channel.EMAIL),  # 20:00
    ],
)
def test_spec_test_4_sms_to_anna_between_8_and_20_her_time(utc, expected):
    """Spec §13 test 4: SMS to Anna between 08:00 and 20:00 in HER time zone, otherwise email."""
    assert plan_targets(None, DIRECTORY, ANNA_SCHEDULE, utc) == [("anna", expected)]


NOON = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("user", "severity", "owner_team", "expected"),
    [
        ("bob", Severity.WARNING, "rates", ["email"]),  # own preferences win over the team's
        ("bob", Severity.CRITICAL, "rates", ["email"]),  # critical not set: falls back to warning
        ("anna", Severity.CRITICAL, "payments", ["phone"]),  # owner team's defaults
        ("anna", Severity.CRITICAL, "rates", ["email"]),  # rates defaults: critical -> info level
        ("anna", Severity.CRITICAL, None, ["phone"]),  # not the owner's member: first team by id (payments)
        ("frank", Severity.CRITICAL, None, sorted(DEFAULT_PREFERENCES["critical"])),  # no team at all
    ],
)
def test_preferences_precedence(user, severity, owner_team, expected):
    targets = plan_targets(None, DIRECTORY, [{"to": {"user": user}}], NOON, severity, owner_team)
    assert targets == [(user, Channel(c)) for c in expected]


def test_rule_channels_win_over_preferences():
    targets = plan_targets(None, DIRECTORY, [{"to": {"user": "bob"}, "channels": ["sms"]}], NOON, Severity.WARNING)
    assert targets == [("bob", Channel.SMS)]


def test_oncall_uses_rotation():
    when = local(2026, 11, 3, 10, 0)  # dave's shift
    targets = plan_targets(None, DIRECTORY, [{"to": {"oncall": "settlements"}, "channels": ["phone", "sms"]}], when)
    assert targets == [("dave", Channel.PHONE), ("dave", Channel.SMS)]


def test_preferences_can_be_a_schedule():
    directory = StaticDirectory(
        teams={"rates": {"anna": "member"}},
        timezones={"anna": "Europe/Warsaw"},
        user_preferences={
            "anna": {
                "critical": {"schedule": [{"between": "09:00-17:00", "days": [1, 2, 3, 4, 5], "use": ["sms"]}],
                             "otherwise": ["phone"]},
            }
        },
    )  # fmt: skip
    route = [{"to": {"user": "anna"}}]
    assert plan_targets(None, directory, route, local(*FRI, 10)) == [("anna", Channel.SMS)]
    assert plan_targets(None, directory, route, local(*SAT, 10)) == [("anna", Channel.PHONE)]


# --- the same against Postgres -------------------------------------------------------------------------


@pytest.fixture(scope="module")
def database():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    with temporary_database("test_prefs", migrate=True) as conninfo:
        yield conninfo


@pytest.fixture
def conn(database):
    """DIRECTORY loaded into the database."""
    with psycopg.connect(database) as conn:
        conn.execute("TRUNCATE notifications, oncall_schedules, channel_preferences, team_members, teams, users")
        users = sorted({u for members in DIRECTORY.teams.values() for u in members} | DIRECTORY.extra_users)
        for user in users:
            conn.execute(
                "INSERT INTO users (id, name, timezone) VALUES (%s, %s, %s)",
                (user, user.title(), DIRECTORY.timezones.get(user, "UTC")),
            )
        for team, members in DIRECTORY.teams.items():
            conn.execute("INSERT INTO teams (id, name) VALUES (%s, %s)", (team, team.title()))
            for user, role in members.items():
                conn.execute("INSERT INTO team_members VALUES (%s, %s, %s)", (team, user, role))
        r = DAILY
        conn.execute(
            "INSERT INTO oncall_schedules (team_id, rotation, shift_length, handoff_time, timezone, starts_on)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            (r.team_id, list(r.users), r.shift_length, r.handoff_time, r.timezone, r.starts_on),
        )
        for owner_type, prefs in (("user", DIRECTORY.user_preferences), ("team", DIRECTORY.team_preferences)):
            for owner_id, by_severity in prefs.items():
                conn.execute(
                    "INSERT INTO channel_preferences (owner_type, owner_id, by_severity) VALUES (%s, %s, %s)",
                    (owner_type, owner_id, Jsonb(by_severity)),
                )
        conn.commit()
        yield conn


@pytest.mark.db
def test_sql_directory_matches_in_memory_directory(conn):
    sql = SqlDirectory()
    for when in (local(2026, 11, 3, 8, 59), local(2026, 11, 3, 9, 0), local(2026, 11, 4, 12, 0)):
        assert sql.on_call(conn, "settlements", when) == DIRECTORY.on_call(None, "settlements", when)
    assert sql.on_call(conn, "rates", NOON) == ["anna", "bob"]  # no rotation: whole team
    users = ["anna", "bob", "carol", "frank", "ghost"]
    for owner in (None, "rates", "payments"):
        assert sql.profiles(conn, users, owner) == DIRECTORY.profiles(None, users, owner)


@pytest.mark.db
def test_planner_with_rotation_zone_and_preferences(conn):
    planner = NotificationPlanner(SqlDirectory())
    rule = RuleForPlanning(
        id="r",
        owner={"team": "payments"},
        route=[
            {"to": {"oncall": "settlements"}, "channels": ["phone"]},
            {"to": {"team": "payments"}},  # preferences
            *ANNA_SCHEDULE,
        ],
    )
    alert = AlertForPlanning(id=1, rule_id="r", severity=Severity.CRITICAL, first_event_id="evt-1")
    when = datetime(2026, 11, 3, 14, 0, tzinfo=UTC)  # dave on call; 09:00 in New York

    result = planner(conn, alert, rule, when)

    assert [(n.user_id, n.channel) for n in result.notifications] == [
        ("anna", Channel.PHONE),  # payments defaults, critical -> phone
        ("anna", Channel.SMS),  # rule schedule, 09:00 her time
        ("dave", Channel.PHONE),  # on call
        ("erin", Channel.PHONE),  # payments defaults
    ]
