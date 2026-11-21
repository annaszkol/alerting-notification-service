"""Tests for A's migrations (owner A, spec §5, §13).

The runner itself is covered by `db/test_migrate.py`; this file only asserts that
the tables A added in `db/migrations/20261122*` behave the way the processor relies
on. Every test runs against a throwaway database with all repo migrations applied.
"""

from __future__ import annotations

from datetime import UTC, datetime

import psycopg
import pytest

from db.testing import TEST_DATABASE_URL, temporary_database


@pytest.fixture
def database():
    """Fresh database with every migration applied (A's and B's)."""
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    with temporary_database("test_a_migrations", migrate=True) as conninfo:
        yield conninfo


# --- helpers ------------------------------------------------------------------------------------------


_INSERT_ALERT = """
    INSERT INTO alerts (
        fingerprint, rule_id, team_id, state, title, body, first_event_id, count,
        resolved_at, resolved_how
    ) VALUES (%s, 'r1', 't1', %s, 'title', 'body', 'e1', 1, %s, %s)
"""


def _insert_alert(conn, fingerprint: str, state: str) -> None:
    """Insert an alert row honoring the resolved <-> resolved_at CHECK constraint."""
    is_resolved = state == "resolved"
    conn.execute(
        _INSERT_ALERT,
        (
            fingerprint,
            state,
            datetime.now(UTC) if is_resolved else None,
            "manual" if is_resolved else None,
        ),
    )


def _version(conn) -> int:
    return conn.execute("SELECT version FROM config_version WHERE id = 1").fetchone()[0]


# --- alerts -------------------------------------------------------------------------------------------


@pytest.mark.db
def test_alerts_allow_only_one_open_per_fingerprint(database):
    """AGENTS.md invariant: partial unique index catches the second open alert."""
    with psycopg.connect(database) as conn:
        _insert_alert(conn, "fp1", "open")
        conn.commit()

    with psycopg.connect(database) as conn:
        with pytest.raises(psycopg.errors.UniqueViolation):
            _insert_alert(conn, "fp1", "open")


@pytest.mark.db
def test_alerts_allow_resolved_duplicates(database):
    """History keeps every resolved alert with the same fingerprint."""
    with psycopg.connect(database) as conn:
        _insert_alert(conn, "fp1", "resolved")
        _insert_alert(conn, "fp1", "resolved")
        conn.commit()
        # An open alert with the same fingerprint is still fine after resolving.
        _insert_alert(conn, "fp1", "open")
        conn.commit()


@pytest.mark.db
def test_alerts_reject_resolved_without_timestamp(database):
    """CHECK: state = 'resolved' implies resolved_at IS NOT NULL."""
    with psycopg.connect(database) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                """
                INSERT INTO alerts (
                    fingerprint, rule_id, team_id, state, title, body, first_event_id
                ) VALUES ('fp1', 'r1', 't1', 'resolved', 't', 'b', 'e1')
                """
            )


@pytest.mark.db
def test_alerts_reject_no_owner(database):
    """CHECK: every alert has team_id or owner_user_id."""
    with psycopg.connect(database) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                """
                INSERT INTO alerts (
                    fingerprint, rule_id, state, title, body, first_event_id
                ) VALUES ('fp1', 'r1', 'open', 't', 'b', 'e1')
                """
            )


# --- processed_events ---------------------------------------------------------------------------------


@pytest.mark.db
def test_processed_events_primary_key_blocks_replay(database):
    """Replay of the same event_id must fail at the DB, not silently accumulate."""
    with psycopg.connect(database) as conn:
        conn.execute("INSERT INTO processed_events (event_id) VALUES ('e1')")
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute("INSERT INTO processed_events (event_id) VALUES ('e1')")


# --- config_version triggers --------------------------------------------------------------------------


@pytest.mark.db
def test_config_version_bumps_on_rule_insert(database):
    with psycopg.connect(database) as conn:
        v0 = _version(conn)
        conn.execute(
            "INSERT INTO rules (id, name, team_id, condition, presentation) "
            "VALUES ('r1', 'R1', 't1', '{}'::jsonb, '{}'::jsonb)"
        )
        assert _version(conn) == v0 + 1


@pytest.mark.db
def test_config_version_bumps_on_schema_and_source_insert(database):
    with psycopg.connect(database) as conn:
        v0 = _version(conn)
        conn.execute(
            "INSERT INTO schemas (id, detect, parser, mapping) "
            "VALUES ('s1', '{}'::jsonb, 'json', '{}'::jsonb)"
        )
        conn.execute("INSERT INTO sources (id, kind) VALUES ('src1', 'http')")
        assert _version(conn) == v0 + 2


@pytest.mark.db
def test_config_version_bumps_on_delete(database):
    with psycopg.connect(database) as conn:
        conn.execute("INSERT INTO sources (id, kind) VALUES ('src1', 'http')")
        v0 = _version(conn)
        conn.execute("DELETE FROM sources WHERE id = 'src1'")
        assert _version(conn) == v0 + 1


@pytest.mark.db
def test_config_version_is_statement_level(database):
    """One bulk INSERT bumps the version once, not once per row."""
    with psycopg.connect(database) as conn:
        v0 = _version(conn)
        conn.execute("INSERT INTO sources (id, kind) VALUES ('s1', 'http'), ('s2', 'http'), ('s3', 'http')")
        assert _version(conn) == v0 + 1


# --- tailer_offsets -----------------------------------------------------------------------------------


@pytest.mark.db
def test_tailer_offsets_requires_source(database):
    with psycopg.connect(database) as conn:
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            conn.execute(
                "INSERT INTO tailer_offsets (source_id, path, byte_offset) VALUES ('missing', '/var/log/x.log', 0)"
            )


@pytest.mark.db
def test_tailer_offsets_cascades_on_source_delete(database):
    with psycopg.connect(database) as conn:
        conn.execute("INSERT INTO sources (id, kind) VALUES ('src1', 'tailer')")
        conn.execute(
            "INSERT INTO tailer_offsets (source_id, path, byte_offset) VALUES ('src1', '/var/log/x.log', 42)"
        )
        conn.commit()
        conn.execute("DELETE FROM sources WHERE id = 'src1'")
        conn.commit()
        assert conn.execute("SELECT count(*) FROM tailer_offsets").fetchone()[0] == 0


# --- schema sanity ------------------------------------------------------------------------------------


@pytest.mark.db
def test_alerts_open_index_exists(database):
    """The partial unique index must exist, or the processor's dedup is not enforced."""
    with psycopg.connect(database) as conn:
        exists = conn.execute(
            "SELECT 1 FROM pg_indexes WHERE tablename = 'alerts' AND indexname = 'alerts_fingerprint_open_uq'"
        ).fetchone()
    assert exists is not None
