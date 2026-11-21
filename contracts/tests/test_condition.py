"""Structural checks for the condition AST (owner A, spec §4, §5).

Evaluation itself lives in `libs/conditions/`; this file only locks in the shape of
the AST and the operator names, so that B, C and the libs cannot drift apart.
"""

from __future__ import annotations

import pytest

from contracts.condition import SEVERITY_FIELD_NAMES, SEVERITY_ORDER, Op


def test_op_enum_matches_spec() -> None:
    assert {op.value for op in Op} == {
        "eq",
        "ne",
        "gt",
        "gte",
        "lt",
        "lte",
        "in",
        "not_in",
        "glob",
        "regex",
        "contains",
        "exists",
    }


def test_severity_order_matches_delivery() -> None:
    """Severity names must match `contracts.delivery.Severity` values."""
    from contracts.delivery import Severity

    assert set(SEVERITY_ORDER) == {s.value for s in Severity}


def test_severity_order_is_ascending() -> None:
    ordered = sorted(SEVERITY_ORDER, key=SEVERITY_ORDER.__getitem__)
    assert ordered == ["info", "warning", "error", "critical"]


def test_severity_field_names_include_severity() -> None:
    assert "severity" in SEVERITY_FIELD_NAMES


@pytest.mark.parametrize(
    "condition",
    [
        {"all": []},
        {"any": []},
        {"not": {"field": "x", "op": "eq", "value": 1}},
        {"field": "x", "op": "eq", "value": 1},
        {
            "all": [
                {"field": "severity", "op": "gte", "value": "error"},
                {"not": {"field": "environment", "op": "eq", "value": "test"}},
            ]
        },
    ],
)
def test_conditions_from_spec_are_syntactically_valid(condition: dict) -> None:
    """Every example from spec §4 must parse to one of the four allowed shapes."""

    def shape(node: dict) -> str:
        keys = set(node)
        if keys == {"all"}:
            return "all"
        if keys == {"any"}:
            return "any"
        if keys == {"not"}:
            return "not"
        if keys == {"field", "op", "value"}:
            return "comparison"
        return "invalid"

    def walk(node: dict) -> None:
        assert shape(node) != "invalid", node
        if "all" in node:
            for child in node["all"]:
                walk(child)
        elif "any" in node:
            for child in node["any"]:
                walk(child)
        elif "not" in node:
            walk(node["not"])

    walk(condition)


def test_comparison_rejects_unknown_op() -> None:
    """The AST is a contract; an unknown operator must be rejected at save time."""
    from contracts.condition import Op

    with pytest.raises(ValueError):
        Op("lt_or_eq")  # type: ignore[arg-type]
