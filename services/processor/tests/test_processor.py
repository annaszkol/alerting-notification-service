"""Processor against Postgres: spec §5 algorithm and §13 acceptance tests 1-5, 7-9, 14 (processor side)."""

import threading
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from db.testing import TEST_DATABASE_URL, temporary_database
from deploy.seed.load import DEFAULT_FILES, apply, load_files
from libs.conditions import Envelope
from libs.conditions import spec_examples as ex
from services.delivery.schedule import parse_window
from services.processor import config as config_store
from services.processor.batch import fingerprint, process_batch
from services.processor.consumer import decode, handle

NOW = datetime(2026, 11, 2, 10, 0, tzinfo=UTC)  # dave on call in settlements; 11:00 in Warsaw (Anna: SMS)
SOURCE_RULE = {
    "id": "settlements-from-kafka",
    "owner": {"team": "settlements"},
    "when": {"all": [{"$sourceId": "workflows-kafka"}, {"type": "WORKFLOW_FAILED"}]},
    "route": [],
}


def rule_row(rule):
    after = rule.get("autoResolveAfter")
    return {
        "id": rule["id"],
        "owner": Jsonb(rule["owner"]),
        "when": Jsonb(rule["when"]),
        "route": Jsonb(rule.get("route", [])),
        "present": Jsonb(rule.get("present", {})),
        "dedup": Jsonb(rule.get("dedup", {})),
        "auto_resolve_after": parse_window(after) if after else None,
        "runbook_ids": rule.get("runbooks", []),
    }


@pytest.fixture(scope="module")
def database():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    with temporary_database("test_processor", migrate=True) as conninfo:
        with psycopg.connect(conninfo) as conn:
            apply(conn, load_files(DEFAULT_FILES))  # people, teams, on-call, preferences, runbooks
            for rule in [*ex.RULES, SOURCE_RULE]:
                conn.execute(
                    """INSERT INTO rules (id, owner, "when", route, present, dedup, auto_resolve_after, runbook_ids,
                                          updated_by)
                       VALUES (%(id)s, %(owner)s, %(when)s, %(route)s, %(present)s, %(dedup)s, %(auto_resolve_after)s,
                               %(runbook_ids)s, 'test')""",
                    rule_row(rule),
                )
            for m in ex.MAPPINGS:
                conn.execute(
                    "INSERT INTO schema_mappings (id, priority, source_ids, detect, schema_from, split_on, fields)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (m["name"], m["priority"], m.get("sources"), Jsonb(m.get("detect")), m.get("schemaFrom"),
                     m.get("splitOn"), Jsonb(m["fields"])),
                )  # fmt: skip
            conn.execute(
                "INSERT INTO sources (id, type, parser) VALUES ('http-default', 'http', 'json'),"
                " ('workflows-kafka', 'kafka', 'json'), ('pricing-logs', 'logfile', 'logfmt')"
            )
        yield conninfo


@pytest.fixture
def conn(database):
    with psycopg.connect(database) as conn:
        conn.execute(
            "TRUNCATE alerts, processed_events, notifications, unmapped_samples, audit_log, runbook_runs,"
            " step_runs RESTART IDENTITY CASCADE"
        )
        conn.commit()
        yield conn


@pytest.fixture
def config(database):
    with psycopg.connect(database) as conn:
        return config_store.load(conn)


counter = iter(range(1, 10**9))


def env(payload, source="http-default", event_id=None, content_type="application/json", received=ex.RECEIVED):
    return Envelope(event_id or f"evt-{next(counter)}", source, received, content_type, payload)


def log_env(line):
    return env(line, "pricing-logs", content_type="text/logfmt")


def run(conn, envelopes, config, now=NOW):
    with conn.transaction():
        return process_batch(conn, envelopes, config, now)


def alerts(conn):
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute("SELECT * FROM alerts ORDER BY id").fetchall()


def notifications(conn, alert_id=None):
    return conn.execute(
        "SELECT user_id, channel FROM notifications WHERE kind = 'alert' AND (%s::bigint IS NULL OR alert_id = %s)"
        " ORDER BY user_id, channel",
        (alert_id, alert_id),
    ).fetchall()


# --- spec §13 v1 tests ---------------------------------------------------------------------------------


def test_1_workflow_event_pages_settlements_on_call(conn, config):
    result = run(conn, [env(ex.WORKFLOW_EVENT)], config)
    [alert] = alerts(conn)
    assert result.created == [alert["id"]]
    assert (alert["rule_id"], alert["owner"], alert["severity"], alert["color"]) == (
        "settlements-prod-failures", {"team": "settlements"}, "error", "red",
    )  # fmt: skip
    assert alert["title"] == "Settlement failed: WF-20261023-00042"
    assert alert["body"] == "Step validate-counterparty: Counterparty service did not respond in 5000 ms"
    assert alert["fields"] == ["step", "error.message"]
    assert notifications(conn) == [("dave", "phone"), ("dave", "sms")]  # on call on 2026-11-02
    audit = conn.execute("SELECT actor, action FROM audit_log WHERE target_id = %s", (str(alert["id"]),)).fetchall()
    assert ("processor", "alert.created") in audit


def test_2_pricing_warnings_group_per_window_without_messages(conn, config):
    line2 = ex.PRICING_LINE
    line1 = line2.replace("pricing-engine-2", "pricing-engine-1").replace("09:14:02", "09:13:10")
    next_window = line2.replace("09:14:02", "09:21:00")
    run(conn, [log_env(line1), log_env(line2)], config)
    [alert] = alerts(conn)
    assert (alert["count"], alert["rule_id"]) == (2, "pricing-warnings")
    assert alert["title"] == "EURUSD: {{$count}} ostrzeżeń z pricing-engine"  # live field kept for display
    assert alert["auto_resolve_after"] == timedelta(minutes=30)
    assert notifications(conn) == []  # route [] = UI only
    run(conn, [log_env(next_window)], config)
    assert len(alerts(conn)) == 2  # next 5-minute window = new alert


def test_3_large_rejection_emails_rates_small_one_does_nothing(conn, config):
    small = {**ex.BUSINESS_EVENT, "payload": {**ex.BUSINESS_EVENT["payload"], "tradeId": "T-1", "notional": 5000000}}
    run(conn, [env(ex.BUSINESS_EVENT), env(small)], config)
    [alert] = alerts(conn)
    assert alert["title"] == "Rejected EUR 25000000 — LIMIT_BREACH"
    assert notifications(conn) == [("anna", "email"), ("bob", "email")]


def test_4_and_5_payments_critical_sms_to_anna_then_resolved_by_event(conn, config):
    run(conn, [env(ex.ALERTMANAGER_ALERT)], config)
    [alert] = alerts(conn)
    assert (alert["owner"], alert["correlation_key"]) == ({"user": "anna"}, "HighErrorRate|payments-api|eu-west-1")
    assert notifications(conn) == [("anna", "sms")]  # 11:00 in Warsaw
    result = run(conn, [env({**ex.ALERTMANAGER_ALERT, "status": "resolved"})], config, NOW + timedelta(minutes=5))
    [alert] = alerts(conn)
    assert result.resolved == [alert["id"]]
    assert (alert["state"], alert["resolved_how"]) == ("resolved", "event")


def test_5_resolved_event_older_than_a_refire_keeps_the_alert_open(conn, config):
    early = {**ex.ALERTMANAGER_ALERT, "status": "resolved", "startsAt": "2026-10-23T09:00:00Z"}
    run(conn, [env(early), env(ex.ALERTMANAGER_ALERT)], config)  # order inside the batch does not matter
    assert [a["state"] for a in alerts(conn)] == ["open"]


def test_7_source_condition(conn, config):
    run(conn, [env(ex.WORKFLOW_EVENT, "workflows-kafka"), env({**ex.WORKFLOW_EVENT, "workflowId": "WF-2"})], config)
    by_rule = sorted((a["rule_id"], a["first_event"]["$sourceId"]) for a in alerts(conn))
    assert by_rule == [
        ("settlements-from-kafka", "workflows-kafka"),
        ("settlements-prod-failures", "http-default"),
        ("settlements-prod-failures", "workflows-kafka"),
    ]


# --- spec §13 v2: dedup, replay, two processors ------------------------------------------------------


def test_8_500_identical_events_two_processors_one_alert(conn, config, database):
    events = [env(ex.WORKFLOW_EVENT) for _ in range(500)]
    errors = []

    def processor(part):
        try:
            with psycopg.connect(database) as own:
                for i in range(0, len(part), 50):
                    handle(own, part[i : i + 50], config, consumer=type("C", (), {"commit": lambda self: None})())
        except Exception as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=processor, args=(events[i::2],)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    [alert] = alerts(conn)
    assert alert["count"] == 500
    assert notifications(conn) == [("dave", "phone"), ("dave", "sms")]  # one series of notifications


def test_9_replay_changes_nothing(conn, config):
    events = [env(ex.WORKFLOW_EVENT) for _ in range(20)]
    run(conn, events, config)
    before = (alerts(conn)[0]["count"], notifications(conn))
    replay = run(conn, events, config)  # reading the topic again within 24 h
    assert (replay.new_events, replay.duplicate_events, replay.changed) == (0, 20, [])
    assert (alerts(conn)[0]["count"], notifications(conn)) == before


def test_same_event_twice_in_one_batch_counts_once(conn, config):
    event = env(ex.WORKFLOW_EVENT)
    run(conn, [event, event], config)
    assert alerts(conn)[0]["count"] == 1


def test_new_alert_after_resolve_links_the_previous_one(conn, config):
    run(conn, [env(ex.WORKFLOW_EVENT)], config)
    conn.execute("UPDATE alerts SET state = 'resolved', resolved_at = now(), resolved_how = 'manual'")
    run(conn, [env(ex.WORKFLOW_EVENT)], config)
    first, second = alerts(conn)
    assert (second["state"], second["previous_alert_id"], second["count"]) == ("open", first["id"], 1)


def test_unmapped_samples_grouped_by_shape(conn, config):
    result = run(conn, [env({"foo": 1, "bar": {"x": 1}}), env({"foo": 2, "bar": {"x": 9}}), env({"other": 1})], config)
    assert result.unmapped == 3
    counts = sorted(r[0] for r in conn.execute("SELECT count FROM unmapped_samples").fetchall())
    assert counts == [1, 2]


def test_auto_runbook_starts_with_the_alert(conn, database):
    with psycopg.connect(database) as admin:
        admin.execute("UPDATE rules SET runbook_ids = '{settlement-timeout}' WHERE id = 'settlements-prod-failures'")
        cfg = config_store.load(admin)
        try:
            run(conn, [env(ex.WORKFLOW_EVENT)], cfg)
            run_row = conn.execute("SELECT runbook_id, mode, started_by, status FROM runbook_runs").fetchone()
            assert run_row == ("settlement-timeout", "auto", None, "running")  # seeded: approved, mode auto
        finally:
            admin.execute("UPDATE rules SET runbook_ids = '{}' WHERE id = 'settlements-prod-failures'")


# --- pieces --------------------------------------------------------------------------------------------


def test_config_reload_and_missing_tables(database):
    with temporary_database("test_processor_empty") as empty, psycopg.connect(empty) as c:
        assert not config_store.tables_ready(c)  # before migrations: the processor waits
        assert config_store.load(c).rules == ()
    with temporary_database("test_processor_fresh", migrate=True) as fresh, psycopg.connect(fresh) as c:
        assert config_store.tables_ready(c)
        assert config_store.load(c).rules == ()  # migrated, nothing configured yet
    with psycopg.connect(database) as c:
        assert config_store.tables_ready(c)
        before = config_store.current_version(c)
        c.execute("UPDATE config_version SET version = version + 1")
        assert config_store.current_version(c) == before + 1
        c.rollback()


def test_fingerprint_window_is_clock_aligned(config):
    rule = next(r for r in config.rules if r.id == "pricing-warnings")
    letter = {"instrument": "EURUSD", "payload": {"instrument": "EURUSD"}, "$eventTime": "2026-10-23T09:14:59Z"}
    same = {**letter, "$eventTime": "2026-10-23T09:10:00Z"}
    other = {**letter, "$eventTime": "2026-10-23T09:15:00Z"}
    assert fingerprint(rule, letter) == fingerprint(rule, same) != fingerprint(rule, other)


def test_decode_envelopes():
    camel = decode(b'{"eventId": "e1", "sourceId": "s", "receivedAt": "2026-10-23T09:14:03Z", "payload": {"a": 1}}')
    snake = decode({"event_id": "e2", "source_id": "s", "content_type": "text/logfmt", "payload": "a=1"})
    assert (camel.event_id, camel.content_type, camel.payload) == ("e1", "application/json", {"a": 1})
    assert (snake.event_id, snake.content_type) == ("e2", "text/logfmt")
    assert decode(b"not json") is None
    assert decode(b'{"payload": 1}') is None


def test_offset_committed_only_after_the_transaction(conn, config):
    calls = []

    class Consumer:
        def commit(self):
            calls.append(conn.execute("SELECT count(*) FROM alerts").fetchone()[0])

    handle(conn, [env(ex.WORKFLOW_EVENT)], config, Consumer())
    assert calls == [1]  # the alert was already committed and visible when the offset was committed
