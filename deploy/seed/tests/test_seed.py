import copy
import json
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest
from jsonschema import Draft202012Validator

from contracts.delivery import AlertForPlanning, Channel, RuleForPlanning, Severity
from db.testing import TEST_DATABASE_URL, temporary_database
from deploy.seed.load import DEFAULT_FILES, apply, load_files, main, parse_duration, validate
from services.delivery.directory import SqlDirectory
from services.delivery.planning import NotificationPlanner

SEED = load_files(DEFAULT_FILES)
ROUTE_SCHEMA = json.loads((Path(__file__).resolve().parents[3] / "contracts" / "route.schema.json").read_text())


def team(team_id):
    return next(t for t in SEED["teams"] if t["id"] == team_id)


# --- the demo file -------------------------------------------------------------------------------------


def test_demo_seed_is_valid():
    assert validate(SEED) == []


def test_demo_seed_covers_spec_scenarios():
    teams = {t["id"] for t in SEED["teams"]}
    assert {"settlements", "pricing", "rates", "payments", "counterparty-support"} <= teams
    # test 14: one person in two teams whose rules can match the same event
    assert "anna" in team("rates")["members"] and "anna" in team("payments")["members"]
    # test 1: settlements has an on-call rotation
    assert team("settlements")["oncall"]["rotation"]
    # approvals need a manager in every team
    for t in SEED["teams"]:
        assert "manager" in t["members"].values(), t["id"]
    # inbound SMS codes are matched by phone: everyone except the admin has one
    assert all(u.get("phone") for u in SEED["users"] if u["id"] != "admin")


def test_preference_values_match_the_route_channel_format():
    """Preferences use the same channel list / schedule format as rules (contracts/route.schema.json)."""
    defs = ROUTE_SCHEMA["$defs"]
    schema = {"$defs": defs, "oneOf": [{"$ref": "#/$defs/channelList"}, {"$ref": "#/$defs/channelSchedule"}]}
    validator = Draft202012Validator(schema)
    all_prefs = [t["preferences"] for t in SEED["teams"] if "preferences" in t] + list(SEED["preferences"].values())
    for prefs in all_prefs:
        for spec in prefs["by_severity"].values():
            validator.validate(spec)


# --- validation catches mistakes -----------------------------------------------------------------------


def broken(mutate):
    data = copy.deepcopy(SEED)
    mutate(data)
    return validate(data)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda d: d["users"].append(dict(d["users"][1])), "defined twice"),
        (lambda d: d["users"][1].update(id="Anna K"), "not a slug"),
        (lambda d: d["users"][2].update(phone=d["users"][1]["phone"]), "used twice"),
        (lambda d: d["teams"][0]["members"].update(ghost="member"), "unknown member"),
        (lambda d: d["teams"][0]["members"].update(dave="boss"), "unknown role"),
        (lambda d: d["teams"][0]["oncall"]["rotation"].append("anna"), "not a team member"),
        (lambda d: d["teams"][0]["oncall"].update(shift_length="one day"), "bad duration"),
        (lambda d: d["teams"][0]["preferences"]["by_severity"].update(fatal=["sms"]), "unknown severity"),
        (lambda d: d["teams"][0]["preferences"]["by_severity"].update(error=["pigeon"]), "unknown channels"),
        (
            lambda d: d["preferences"]["anna"]["by_severity"]["critical"]["schedule"][0].update(between="8-20"),
            "bad window",
        ),
        (lambda d: d["teams"][0]["preferences"]["limits"].update(sms={"max": 0, "window": "10m"}), "limits.sms"),
        (lambda d: d["preferences"].update(ghost={"by_severity": {}}), "unknown user"),
    ],
)
def test_validation_errors(mutate, message):
    errors = broken(mutate)
    assert any(message in e for e in errors), errors


def test_parse_duration():
    assert parse_duration("10m").total_seconds() == 600
    assert parse_duration("7d").days == 7
    assert parse_duration("1w").days == 7


def test_main_rejects_invalid_file(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    bad = tmp_path / "bad.yaml"
    bad.write_text("teams:\n  - {id: x, members: {ghost: member}}\n")
    assert main([str(bad)]) == 1


def test_main_needs_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert main([]) == 2


# --- loading into Postgres -----------------------------------------------------------------------------


@pytest.fixture
def database():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    with temporary_database("test_seed", migrate=True) as conninfo:
        yield conninfo


def counts(conninfo):
    with psycopg.connect(conninfo) as conn:
        return {
            table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("users", "teams", "team_members", "oncall_schedules", "channel_preferences")
        }


@pytest.mark.db
def test_load_is_idempotent_and_keeps_ui_edits(database, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", database)
    assert main([]) == 0
    loaded = counts(database)
    assert loaded == {
        "users": len(SEED["users"]),
        "teams": len(SEED["teams"]),
        "team_members": sum(len(t["members"]) for t in SEED["teams"]),
        "oncall_schedules": sum(1 for t in SEED["teams"] if "oncall" in t),
        "channel_preferences": sum(1 for t in SEED["teams"] if "preferences" in t) + len(SEED["preferences"]),
    }

    with psycopg.connect(database) as conn:  # someone edits their profile in the UI
        conn.execute("UPDATE users SET timezone = 'Asia/Tokyo' WHERE id = 'anna'")

    with psycopg.connect(database) as conn:
        assert sum(apply(conn, SEED).values()) == 0  # nothing new
        assert conn.execute("SELECT timezone FROM users WHERE id = 'anna'").fetchone()[0] == "Asia/Tokyo"

    assert main(["--reset"]) == 0
    with psycopg.connect(database) as conn:
        assert conn.execute("SELECT timezone FROM users WHERE id = 'anna'").fetchone()[0] == "Europe/Warsaw"
    assert counts(database) == loaded


@pytest.mark.db
def test_seeded_routing_for_spec_scenarios(database):
    """Spec §13 tests 1, 4 and 14 against the seeded directory."""
    with psycopg.connect(database) as conn:
        apply(conn, SEED)
        planner = NotificationPlanner(SqlDirectory())

        def plan(rule, alert_id, when, event="evt-1", severity=Severity.CRITICAL):
            alert = AlertForPlanning(id=alert_id, rule_id=rule.id, severity=severity, first_event_id=event)
            return [(n.user_id, n.channel) for n in planner(conn, alert, rule, when).notifications]

        # test 1: settlements on-call (dave on 2026-11-03 after 09:00 Warsaw) gets phone + SMS
        settlements = RuleForPlanning(
            "settlements-prod-failures",
            {"team": "settlements"},
            [{"to": {"oncall": "settlements"}, "channels": ["phone", "sms"]}],
        )
        assert plan(settlements, 1, datetime(2026, 11, 3, 10, 0, tzinfo=UTC)) == [
            ("dave", Channel.PHONE),
            ("dave", Channel.SMS),
        ]

        # test 4: anna, 08:00-20:00 Warsaw -> SMS, otherwise email
        anna = RuleForPlanning(
            "anna-payments-critical",
            {"user": "anna"},
            [
                {
                    "to": {"user": "anna"},
                    "channels": {"schedule": [{"between": "08:00-20:00", "use": ["sms"]}], "otherwise": ["email"]},
                }
            ],
        )
        assert plan(anna, 2, datetime(2026, 11, 21, 12, 0, tzinfo=UTC), event="evt-2") == [("anna", Channel.SMS)]
        assert plan(anna, 3, datetime(2026, 11, 21, 20, 0, tzinfo=UTC), event="evt-3") == [("anna", Channel.EMAIL)]

        # test 14: anna in rates and payments; both teams' rules match one event -> one SMS for anna
        rates = RuleForPlanning("rates-x", {"team": "rates"}, [{"to": {"team": "rates"}, "channels": ["sms"]}])
        payments = RuleForPlanning(
            "payments-x", {"team": "payments"}, [{"to": {"team": "payments"}, "channels": ["sms"]}]
        )
        noon = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
        first = plan(rates, 4, noon, event="evt-14")
        second = plan(payments, 5, noon, event="evt-14")
        assert first == [("anna", Channel.SMS), ("bob", Channel.SMS)]
        assert second == [("erin", Channel.SMS)]
        conn.rollback()
