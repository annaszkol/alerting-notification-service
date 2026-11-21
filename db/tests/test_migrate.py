from pathlib import Path

import psycopg
import pytest

from db.migrate import MIGRATIONS_DIR, MigrationError, discover, migrate
from db.testing import TEST_DATABASE_URL, temporary_database

# --- file discovery (no database needed) -------------------------------------------------------------


def write(directory: Path, name: str, sql: str = "SELECT 1;") -> None:
    (directory / name).write_text(sql, encoding="utf-8")


def test_discover_sorts_by_version(tmp_path):
    write(tmp_path, "202611211200_second.sql")
    write(tmp_path, "202611210900_first.sql")
    assert [m.name for m in discover(tmp_path)] == ["first", "second"]


@pytest.mark.parametrize(
    "bad_name",
    ["2026112109_short_version.sql", "202611210900-dash.sql", "202611210900_Upper.sql", "init.sql"],
)
def test_discover_rejects_bad_names(tmp_path, bad_name):
    write(tmp_path, bad_name)
    with pytest.raises(MigrationError, match="bad migration file name"):
        discover(tmp_path)


def test_discover_rejects_duplicate_versions(tmp_path):
    write(tmp_path, "202611210900_alerts.sql")
    write(tmp_path, "202611210900_notifications.sql")
    with pytest.raises(MigrationError, match="duplicate migration version"):
        discover(tmp_path)


def test_repo_migrations_are_valid():
    discover(MIGRATIONS_DIR)


# --- applying migrations (needs TEST_DATABASE_URL) ----------------------------------------------------


@pytest.fixture
def database():
    """A fresh, empty database created for one test and dropped afterwards."""
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    with temporary_database("test_migrate") as conninfo:
        yield conninfo


@pytest.mark.db
def test_applies_in_order_and_only_once(database, tmp_path):
    write(tmp_path, "202611210900_create_t.sql", "CREATE TABLE t (v int); INSERT INTO t VALUES (1);")
    write(tmp_path, "202611211000_insert_two.sql", "INSERT INTO t VALUES (2);")

    assert migrate(database, tmp_path) == ["202611210900", "202611211000"]
    assert migrate(database, tmp_path) == []

    with psycopg.connect(database) as conn:
        assert [r[0] for r in conn.execute("SELECT v FROM t ORDER BY v")] == [1, 2]


@pytest.mark.db
def test_failed_migration_is_rolled_back(database, tmp_path):
    write(tmp_path, "202611210900_ok.sql", "CREATE TABLE ok (v int);")
    write(tmp_path, "202611211000_broken.sql", "CREATE TABLE half (v int); SELECT * FROM missing_table;")

    with pytest.raises(psycopg.errors.UndefinedTable):
        migrate(database, tmp_path)

    with psycopg.connect(database) as conn:
        versions = [r[0] for r in conn.execute("SELECT version FROM schema_migrations")]
        half = conn.execute("SELECT to_regclass('half')").fetchone()[0]
    assert versions == ["202611210900"]
    assert half is None


@pytest.mark.db
def test_refuses_edited_migration(database, tmp_path):
    write(tmp_path, "202611210900_create_t.sql", "CREATE TABLE t (v int);")
    migrate(database, tmp_path)

    write(tmp_path, "202611210900_create_t.sql", "CREATE TABLE t (v bigint);")
    with pytest.raises(MigrationError, match="changed after it was applied"):
        migrate(database, tmp_path)


@pytest.mark.db
def test_applies_late_merged_older_version(database, tmp_path):
    """Branches merge out of order: an older timestamp that was never applied still runs."""
    write(tmp_path, "202611211000_newer.sql", "CREATE TABLE newer (v int);")
    migrate(database, tmp_path)

    write(tmp_path, "202611210900_older.sql", "CREATE TABLE older (v int);")
    assert migrate(database, tmp_path) == ["202611210900"]
