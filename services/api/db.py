"""Connection pool and the per-request connection dependency.

REST handlers are sync (FastAPI runs them in a thread pool) and use psycopg's sync pool, so domain functions
take a plain `psycopg.Connection` and can be shared with code that runs in the same process (e.g. B's inbound
SMS handler). One request = one transaction: committed when the handler returns, rolled back on any error.
"""

from __future__ import annotations

from collections.abc import Iterator

import psycopg
from fastapi import Request
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


def create_pool(database_url: str) -> ConnectionPool:
    return ConnectionPool(
        database_url,
        min_size=1,
        max_size=10,
        kwargs={"row_factory": dict_row},
        open=False,
        name="api",
    )


def get_conn(request: Request) -> Iterator[psycopg.Connection]:
    pool: ConnectionPool = request.app.state.pool
    with pool.connection() as conn:  # commits on normal exit, rolls back on exception
        yield conn
