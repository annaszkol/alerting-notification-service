"""Test helpers: throwaway Postgres databases. Used by pytest fixtures across services."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


@contextmanager
def temporary_database(prefix: str = "test", migrate: bool = False) -> Iterator[str]:
    """Create an empty database (optionally with all migrations applied), yield its conninfo, drop it."""
    if not TEST_DATABASE_URL:
        raise RuntimeError("TEST_DATABASE_URL is not set")
    name = f"{prefix}_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    conninfo = psycopg.conninfo.make_conninfo(TEST_DATABASE_URL, dbname=name)
    try:
        if migrate:
            from db.migrate import migrate as run_migrations

            run_migrations(conninfo)
        yield conninfo
    finally:
        with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
