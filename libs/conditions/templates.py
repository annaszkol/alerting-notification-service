"""Templates (spec §3, AGENTS.md "Templates never execute code").

`{{path}}` reads the payload, `{{$field}}` the letter; a missing value renders as `—`. Live fields (`{{$count}}`,
`{{$state}}`, `{{$alertUrl}}`) are left untouched by `render` and filled by `render_live` at display / send time,
so an alert stores its title once with the live markers intact.
"""

from __future__ import annotations

import json
import re
from typing import Any

from libs.conditions.paths import MISSING, PathError, get_path, parse_path

PLACEHOLDER = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
LIVE_FIELDS = ("$count", "$state", "$alertUrl")
EMPTY = "—"
MAX_RENDERED = 64 * 1024


def _text(value: Any) -> str:
    if value is MISSING or value is None:
        return EMPTY
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, list) and all(not isinstance(v, dict | list) for v in value):
        return ", ".join(_text(v) for v in value)
    if isinstance(value, dict | list):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def render(template: str | None, letter: dict[str, Any]) -> str | None:
    """Static rendering from the letter; live fields stay as `{{$count}}` etc."""
    if template is None:
        return None

    def replace(match: re.Match[str]) -> str:
        path = match.group(1)
        if path in LIVE_FIELDS:
            return match.group(0)
        return _text(get_path(letter, path))

    return PLACEHOLDER.sub(replace, str(template))[:MAX_RENDERED]


def render_live(text: str | None, live: dict[str, Any]) -> str | None:
    """Fill live fields: `live` = {"$count": 5, "$state": "open", "$alertUrl": "..."}."""
    if text is None:
        return None

    def replace(match: re.Match[str]) -> str:
        path = match.group(1)
        return _text(live.get(path, MISSING)) if path in LIVE_FIELDS else match.group(0)

    return PLACEHOLDER.sub(replace, text)


def validate_template(text: Any, where: str) -> list[dict[str, str]]:
    if not isinstance(text, str):
        return [{"path": where, "message": "template must be a string"}]
    errors = []
    if text.count("{{") != text.count("}}"):
        errors.append({"path": where, "message": "unbalanced {{ }}"})
    for m in PLACEHOLDER.finditer(text):
        try:
            parse_path(m.group(1))
        except PathError as exc:
            errors.append({"path": where, "message": str(exc)})
    return errors
