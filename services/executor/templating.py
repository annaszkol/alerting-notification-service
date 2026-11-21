"""Rendering of step parameters with alert values.

Minimal stand-in for the shared templater (libs/conditions, owner A, spec §3) until it lands: `{{path}}` reads the
event payload, `{{$field}}` reads the letter, a missing value renders as `—`. No expressions, no code execution.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

TOKEN = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
PATH = re.compile(r'\["([^"]+)"\]|([^.\[\]]+)')
MISSING = "—"


def lookup(data: Any, path: str) -> Any:
    """`a.b.0.c` and `labels["app.kubernetes.io/name"]` (AGENTS.md path rules)."""
    current = data
    for quoted, plain in PATH.findall(path):
        key = quoted or plain
        if isinstance(current, Mapping) and key in current:
            current = current[key]
        elif isinstance(current, list) and key.isdigit() and int(key) < len(current):
            current = current[int(key)]
        else:
            return None
    return current


def render(template: str, letter: Mapping[str, Any]) -> str:
    payload = letter.get("payload", letter) if isinstance(letter, Mapping) else {}

    def value(match: re.Match[str]) -> str:
        path = match.group(1)
        found = letter.get(path) if path.startswith("$") else lookup(payload, path)
        return MISSING if found is None else str(found)

    return TOKEN.sub(value, template)


def render_params(params: Any, letter: Mapping[str, Any]) -> Any:
    """Render every string inside params (nested dicts / lists included)."""
    if isinstance(params, str):
        return render(params, letter)
    if isinstance(params, Mapping):
        return {key: render_params(value, letter) for key, value in params.items()}
    if isinstance(params, list):
        return [render_params(value, letter) for value in params]
    return params
