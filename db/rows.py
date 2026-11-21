"""Row-shape guard for code shared between processes.

The API pool returns rows as dicts (`row_factory=dict_row`); B's code reads rows as tuples. Public entry points that
other services call with their own connection are wrapped in `@plain_rows`, which switches the connection to tuple
rows for the duration of the call and restores the caller's setting afterwards.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any

import psycopg
from psycopg.rows import tuple_row


def plain_rows[F: Callable[..., Any]](fn: F) -> F:
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        conn = next((a for a in (*args, *kwargs.values()) if isinstance(a, psycopg.Connection)), None)
        if conn is None:
            return fn(*args, **kwargs)
        previous = conn.row_factory
        conn.row_factory = tuple_row
        try:
            return fn(*args, **kwargs)
        finally:
            conn.row_factory = previous

    return wrapper  # type: ignore[return-value]
