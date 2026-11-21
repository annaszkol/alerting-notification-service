"""Shared condition evaluator, templater and event mapping (spec §3, §4). Owner A; written by B with the team's OK.

Function names match `services/api/stubs.py`, so the API can import from here instead without behaviour changes.
"""

from libs.conditions.evaluate import OPERATORS, SEVERITY_ORDER, evaluate, explain, validate_condition
from libs.conditions.mapping import Envelope, Mapped, SchemaMapping, Source, map_event, shape_hash, shape_keys
from libs.conditions.paths import MISSING, PathError, get_in, get_path, parse_path
from libs.conditions.templates import EMPTY, LIVE_FIELDS, render, render_live, validate_template

__all__ = [
    "EMPTY",
    "LIVE_FIELDS",
    "MISSING",
    "OPERATORS",
    "SEVERITY_ORDER",
    "Envelope",
    "Mapped",
    "PathError",
    "SchemaMapping",
    "Source",
    "evaluate",
    "explain",
    "get_in",
    "get_path",
    "map_event",
    "parse_path",
    "render",
    "render_live",
    "shape_hash",
    "shape_keys",
    "validate_condition",
    "validate_template",
]
