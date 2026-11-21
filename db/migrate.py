"""Minimal SQL migration runner.

Applies `db/migrations/<YYYYMMDDHHMM>_<name>.sql` files in version order, each in its own transaction,
and records them in `schema_migrations`. Safe to run from several containers at once (advisory lock).

Rules (AGENTS.md): never edit a merged migration. The runner stores a checksum per file and refuses
to continue when an applied file has changed.

Usage: DATABASE_URL=postgresql://... python -m db.migrate [migrations_dir]
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import psycopg

log = logging.getLogger("migrate")

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
FILENAME_RE = re.compile(r"^(?P<version>\d{12})_(?P<name>[a-z0-9_]+)\.sql$")
# Arbitrary constant; only needs to be unique among advisory locks used by this app.
LOCK_ID = 7_210_001


class MigrationError(Exception):
    pass


@dataclass(frozen=True)
class Migration:
    version: str
    name: str
    path: Path
    sql: str
    checksum: str


def discover(directory: Path) -> list[Migration]:
    """Return migrations sorted by version. Rejects bad names and duplicate versions."""
    migrations: list[Migration] = []
    seen: dict[str, str] = {}
    for path in sorted(directory.glob("*.sql")):
        match = FILENAME_RE.match(path.name)
        if not match:
            raise MigrationError(f"bad migration file name {path.name!r}, expected <YYYYMMDDHHMM>_<name>.sql")
        version = match["version"]
        if version in seen:
            raise MigrationError(f"duplicate migration version {version}: {seen[version]} and {path.name}")
        seen[version] = path.name
        sql = path.read_text(encoding="utf-8")
        migrations.append(
            Migration(
                version=version,
                name=match["name"],
                path=path,
                sql=sql,
                checksum=hashlib.sha256(sql.encode("utf-8")).hexdigest(),
            )
        )
    return sorted(migrations, key=lambda m: m.version)


def migrate(conninfo: str, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply pending migrations. Returns the versions applied in this run."""
    migrations = discover(directory)
    applied_now: list[str] = []
    with psycopg.connect(conninfo, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (LOCK_ID,))
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version    text PRIMARY KEY,
                    name       text NOT NULL,
                    checksum   text NOT NULL,
                    applied_at timestamptz NOT NULL DEFAULT now()
                )
                """
            )
            applied = {
                version: checksum
                for version, checksum in conn.execute("SELECT version, checksum FROM schema_migrations")
            }
            for m in migrations:
                if m.version in applied:
                    if applied[m.version] != m.checksum:
                        raise MigrationError(
                            f"{m.path.name} was changed after it was applied; add a new migration instead"
                        )
                    continue
                log.info("applying %s", m.path.name)
                with conn.transaction():
                    conn.execute(m.sql)
                    conn.execute(
                        "INSERT INTO schema_migrations (version, name, checksum) VALUES (%s, %s, %s)",
                        (m.version, m.name, m.checksum),
                    )
                applied_now.append(m.version)
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_ID,))
    return applied_now


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    conninfo = os.environ.get("DATABASE_URL")
    if not conninfo:
        log.error("DATABASE_URL is not set")
        return 2
    directory = Path(argv[1]) if len(argv) > 1 else MIGRATIONS_DIR
    try:
        applied = migrate(conninfo, directory)
    except MigrationError as exc:
        log.error("%s", exc)
        return 1
    log.info("done, %d migration(s) applied", len(applied))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
