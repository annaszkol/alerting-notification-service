"""Paths (spec §3, AGENTS.md): `a.b` nested, numeric segments index arrays (`a.0` or `a[0]`), keys containing
dots in brackets (`labels["app.kubernetes.io/name"]`, single quotes too). `$field` reads the letter, anything else
reads the payload."""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any


class PathError(ValueError):
    pass


class _Missing:
    def __repr__(self) -> str:
        return "MISSING"

    def __bool__(self) -> bool:
        return False


MISSING: Any = _Missing()

_SEGMENT = re.compile(r"""\[\s*"((?:[^"\\]|\\.)*)"\s*\]|\[\s*'((?:[^'\\]|\\.)*)'\s*\]|\[\s*(\d+)\s*\]|([^.\[\]]+)""")


@lru_cache(maxsize=4096)
def _parse(path: str) -> tuple[str | int, ...]:
    if not isinstance(path, str) or not path.strip():
        raise PathError("empty path")
    path = path.strip()
    segments: list[str | int] = []
    pos = 0
    while pos < len(path):
        if path[pos] == "." and segments:
            pos += 1
        m = _SEGMENT.match(path, pos)
        if not m:
            raise PathError(f"invalid path {path!r} at position {pos}")
        double, single, index, plain = m.groups()
        if double is not None:
            segments.append(double.replace('\\"', '"'))
        elif single is not None:
            segments.append(single.replace("\\'", "'"))
        elif index is not None:
            segments.append(int(index))
        else:
            segments.append(int(plain) if plain.isdigit() else plain)
        pos = m.end()
    return tuple(segments)


def parse_path(path: str) -> list[str | int]:
    """Segments of a path; raises PathError for an invalid one (use when validating rules)."""
    return list(_parse(path))


def walk(node: Any, segments: tuple[str | int, ...] | list[str | int]) -> Any:
    for seg in segments:
        if isinstance(node, dict):
            key = str(seg)
            if key not in node:
                return MISSING
            node = node[key]
        elif isinstance(node, list) and isinstance(seg, int):
            if seg >= len(node):
                return MISSING
            node = node[seg]
        else:
            return MISSING
    return node


def get_path(letter: dict[str, Any], path: str) -> Any:
    """Value at `path` in a letter (`{"$field": ..., "payload": raw}`), or MISSING. Never raises."""
    try:
        segments = _parse(path)
    except (PathError, TypeError):
        return MISSING
    if isinstance(segments[0], str) and segments[0].startswith("$"):
        return walk(letter, segments)
    return walk(letter.get("payload", MISSING) if isinstance(letter, dict) else MISSING, segments)


def get_in(data: Any, path: str) -> Any:
    """Value at `path` in plain data (no `$` rule): used by mappings, which read the parsed payload."""
    try:
        return walk(data, _parse(path))
    except (PathError, TypeError):
        return MISSING
