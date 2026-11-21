"""Stand-ins for `libs/conditions` (owner A) until it is merged. TEMPORARY.

Same function names as proposed to A in docs/plans/c-api-web.md (agreement 6). When `libs/conditions` lands,
the API imports from there and this module is deleted in the same PR. Behaviour follows spec §3 and §4, but
only as far as the API needs it: validating documents, reading fields, rendering live fields.
"""

from __future__ import annotations

import re
from typing import Any

MISSING = object()

OPERATORS = {"eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in", "glob", "regex", "contains", "exists"}
COMBINATORS = {"all", "any", "not"}
LIVE_FIELDS = ("$count", "$state", "$alertUrl")

_SEGMENT = re.compile(r'\[\s*"((?:[^"\\]|\\.)*)"\s*\]|\[\s*(\d+)\s*\]|([^.\[\]]+)')
_TEMPLATE = re.compile(r"{{\s*(.*?)\s*}}")


class PathError(ValueError):
    pass


def parse_path(path: str) -> list[str | int]:
    """`a.b` nested, numeric segments index arrays, `labels["a.b"]` for keys containing dots (spec §3)."""
    if not isinstance(path, str) or not path.strip():
        raise PathError("empty path")
    segments: list[str | int] = []
    pos = 0
    while pos < len(path):
        if path[pos] == "." and segments:
            pos += 1
        m = _SEGMENT.match(path, pos)
        if not m:
            raise PathError(f"invalid path {path!r} at position {pos}")
        quoted, index, plain = m.groups()
        if quoted is not None:
            segments.append(quoted.replace('\\"', '"'))
        elif index is not None:
            segments.append(int(index))
        else:
            segments.append(int(plain) if plain.isdigit() else plain)
        pos = m.end()
    return segments


def get_path(letter: dict[str, Any], path: str) -> Any:
    """Value at `path`: `$field` reads the letter, anything else the raw payload. MISSING if absent."""
    try:
        segments = parse_path(path)
    except PathError:
        return MISSING
    node: Any = letter if str(segments[0]).startswith("$") else letter.get("payload", MISSING)
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


def render_live(text: str | None, live: dict[str, Any]) -> str | None:
    """Fill only the live fields (`{{$count}}`, `{{$state}}`, `{{$alertUrl}}`); the rest was rendered once."""
    if text is None:
        return None

    def repl(m: re.Match[str]) -> str:
        name = m.group(1)
        if name in live:
            return str(live[name])
        return m.group(0)

    return _TEMPLATE.sub(repl, text)


def validate_template(text: Any, where: str) -> list[dict[str, str]]:
    if not isinstance(text, str):
        return [{"path": where, "message": "template must be a string"}]
    errors = []
    if text.count("{{") != text.count("}}"):
        errors.append({"path": where, "message": "unbalanced {{ }}"})
    for m in _TEMPLATE.finditer(text):
        try:
            parse_path(m.group(1))
        except PathError as exc:
            errors.append({"path": where, "message": str(exc)})
    return errors


def validate_condition(cond: Any, where: str = "when") -> list[dict[str, str]]:
    """Structure check of the condition JSON (spec §4). Never raises."""
    if not isinstance(cond, dict) or not cond:
        return [{"path": where, "message": "condition must be a non-empty object"}]
    errors: list[dict[str, str]] = []
    for key, value in cond.items():
        at = f"{where}.{key}"
        if key in ("all", "any"):
            if not isinstance(value, list) or not value:
                errors.append({"path": at, "message": f"`{key}` needs a non-empty list"})
                continue
            for i, child in enumerate(value):
                errors += validate_condition(child, f"{at}.{i}")
        elif key == "not":
            errors += validate_condition(value, at)
        else:
            try:
                parse_path(key)
            except PathError as exc:
                errors.append({"path": at, "message": str(exc)})
                continue
            if isinstance(value, dict):
                errors += _validate_ops(value, at)
    return errors


def _validate_ops(ops: dict[str, Any], at: str) -> list[dict[str, str]]:
    if not ops:
        return [{"path": at, "message": "empty operator object"}]
    errors = []
    for op, arg in ops.items():
        if op not in OPERATORS:
            errors.append({"path": f"{at}.{op}", "message": f"unknown operator `{op}`"})
        elif op in ("in", "not_in") and not isinstance(arg, list):
            errors.append({"path": f"{at}.{op}", "message": f"`{op}` needs a list"})
        elif op == "exists" and not isinstance(arg, bool):
            errors.append({"path": f"{at}.{op}", "message": "`exists` needs true or false"})
        elif op == "regex":
            try:
                re.compile(arg)
            except (re.error, TypeError) as exc:
                errors.append({"path": f"{at}.{op}", "message": f"invalid regex: {exc}"})
        elif op in ("glob", "contains") and not isinstance(arg, str):
            errors.append({"path": f"{at}.{op}", "message": f"`{op}` needs a string"})
    return errors
