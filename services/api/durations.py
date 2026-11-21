"""Durations in documents (`30s`, `5m`, `1h`, `2d`) <-> `timedelta` / Postgres `interval`."""

from __future__ import annotations

import re
from datetime import timedelta

_DURATION = re.compile(r"^(\d+)(s|m|h|d)$")
_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_duration(text: str) -> timedelta:
    m = _DURATION.match(text)
    if not m:
        raise ValueError(f"invalid duration {text!r}")
    return timedelta(seconds=int(m.group(1)) * _UNITS[m.group(2)])


def format_duration(value: timedelta | None) -> str | None:
    if value is None:
        return None
    seconds = int(value.total_seconds())
    for unit in ("d", "h", "m"):
        if seconds and seconds % _UNITS[unit] == 0:
            return f"{seconds // _UNITS[unit]}{unit}"
    return f"{seconds}s"
