"""Condition JSON contract (owner A, spec §4, §5, §13).

A condition is a JSON value that evaluates to true or false against a `Letter.view()`.
It is the same grammar as `route.schema.json` - but where `route` decides *who* to
notify, a condition decides *whether* an event reaches a rule or a schema `detect`.

Grammar (informal; the concrete Python type is `dict[str, Any]`):

    Condition  = { "all": [Condition, ...] }
               | { "any": [Condition, ...] }
               | { "not": Condition }
               | Comparison

    Comparison = { "field": Path, "op": Op, "value": Value }

Examples from spec §4:

    # "any WORKFLOW_FAILED where workflowName = order-settlement and environment = prod"
    {"all": [
        {"field": "type",           "op": "eq", "value": "WORKFLOW_FAILED"},
        {"field": "workflowName",   "op": "eq", "value": "order-settlement"},
        {"field": "environment",    "op": "eq", "value": "prod"},
    ]}

    # "trade.rejected.* where desk = rates and notional > 10,000,000"
    {"all": [
        {"field": "$schema",            "op": "glob", "value": "trade.rejected.*"},
        {"field": "payload.desk",       "op": "eq",   "value": "rates"},
        {"field": "payload.notional",   "op": "gt",   "value": 10_000_000},
    ]}

Evaluation semantics (implemented by `libs/conditions`, contract only here):

  - Never raises. Missing field or wrong type -> false.
  - Empty `all` -> true. Empty `any` -> false.
  - `not` inverts the boolean. `not` of a false comparison is true even when the
    field is missing - that is the only way a missing field produces a positive result
    other than `exists: false`.
  - `exists` expects a boolean value; `exists: false` is true when the field is absent.
  - `gt/gte/lt/lte` compare numbers to numbers and strings to strings. Strings that
    look like numbers ("42") are compared as numbers only if the condition value is a
    number. Type mismatches evaluate to false.
  - `in/not_in` compare scalars against a list using `==` semantics.
  - `glob` is fnmatch-style (`*`, `?`). `regex` is `re.search` with a pattern length
    cap enforced by the evaluator (defense against ReDoS).
  - `contains` is substring for strings and element membership for lists.
  - Severity ordering (`info < warning < error < critical`) is applied when comparing
    severity fields: `{"field": "severity", "op": "gte", "value": "error"}` is true
    for "error" and "critical" and false for "info" and "warning".
"""

from __future__ import annotations

from enum import StrEnum


class Op(StrEnum):
    EQ = "eq"
    NE = "ne"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    IN = "in"
    NOT_IN = "not_in"
    GLOB = "glob"
    REGEX = "regex"
    CONTAINS = "contains"
    EXISTS = "exists"


# Severity order shared with `contracts.delivery.Severity`.
# `gt/gte/lt/lte` on a field whose name is in this map compares severities, not strings.
SEVERITY_FIELD_NAMES = frozenset({"severity", "level", "priority"})
SEVERITY_ORDER: dict[str, int] = {
    "info": 0,
    "warning": 1,
    "error": 2,
    "critical": 3,
}
