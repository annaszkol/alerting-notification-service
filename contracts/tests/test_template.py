"""Structural checks for the template syntax (owner A, spec §3)."""

from __future__ import annotations

import pytest

from contracts.template import MAX_RENDERED_LENGTH, PLACEHOLDER


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("plain text", []),
        ("{{a}}", ["a"]),
        ("Hello {{workflowName}}!", ["workflowName"]),
        ("{{ a.b.c }}", ["a.b.c"]),
        ("{{payload.meta.schema}}", ["payload.meta.schema"]),
        ("{{ $count }}", ["$count"]),
        ("{{$schema}} and {{$state}}", ["$schema", "$state"]),
        ('{{labels["a.b"]}}', ['labels["a.b"]']),
        # Nested braces are not supported by the renderer, but Python's `re` has
        # no way to reject the whole input: it matches the innermost `{{...}}`
        # pair. Rejecting `{{a{{b}}}}` at save time is the renderer's job
        # (`libs/conditions/template.py`), not the regex's. See `templates.md`.
        ("{{a{{b}}}}", ["b"]),
        # Multiple placeholders on one line.
        ("{{a}} {{b}} {{c}}", ["a", "b", "c"]),
    ],
)
def test_placeholder_matches(text: str, expected: list[str]) -> None:
    assert PLACEHOLDER.findall(text) == expected


def test_placeholder_does_not_match_single_braces() -> None:
    assert PLACEHOLDER.findall("{a} {{b}}") == ["b"]


def test_max_rendered_length_is_documented() -> None:
    """A sanity cap, not a strict spec value - keep it large enough for large payloads."""
    assert MAX_RENDERED_LENGTH >= 16 * 1024
    assert MAX_RENDERED_LENGTH <= 1024 * 1024
