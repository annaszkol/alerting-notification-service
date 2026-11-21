"""Condition evaluation (spec §4, AGENTS.md "Condition evaluation never throws").

Grammar (spec §4, the form rules are stored in):
    {"all": [cond, ...]} | {"any": [cond, ...]} | {"not": cond}
    {"<path>": <value>}                      equality
    {"<path>": {"<op>": <arg>, ...}}         every operator must hold
    several keys in one object               all must hold
The `{"field": <path>, "op": <op>, "value": <arg>}` form from contracts/condition.py is accepted too.

Missing field or wrong type -> false, except `exists: false` and `not`. Numbers written as strings compare as
numbers. `gt/gte/lt/lte` between two severity names use info < warning < error < critical.
"""

from __future__ import annotations

import fnmatch
import math
import re
from functools import lru_cache
from typing import Any

from libs.conditions.paths import MISSING, PathError, get_path, parse_path

OPERATORS = {"eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in", "glob", "regex", "contains", "exists"}
COMBINATORS = {"all", "any", "not"}
SEVERITY_ORDER = {"info": 0, "warning": 1, "error": 2, "critical": 3}
MAX_REGEX = 500  # pattern length cap (ReDoS)
MAX_DEPTH = 32


def evaluate(cond: Any, letter: dict[str, Any]) -> bool:
    try:
        return _eval(cond, letter, 0)
    except Exception:  # noqa: BLE001 - the contract: evaluation never throws
        return False


def _eval(cond: Any, letter: dict[str, Any], depth: int) -> bool:
    if depth > MAX_DEPTH or not isinstance(cond, dict) or not cond:
        return False
    if set(cond) >= {"field", "op"} and set(cond) <= {"field", "op", "value"}:  # contracts/condition.py form
        return _compare(get_path(letter, cond["field"]), cond["op"], cond.get("value"))
    for key, value in cond.items():
        if key == "all":
            ok = isinstance(value, list) and all(_eval(c, letter, depth + 1) for c in value)
        elif key == "any":
            ok = isinstance(value, list) and any(_eval(c, letter, depth + 1) for c in value)
        elif key == "not":
            ok = not _eval(value, letter, depth + 1)
        else:
            actual = get_path(letter, key)
            if isinstance(value, dict) and value and set(value) <= OPERATORS:
                ok = all(_compare(actual, op, arg) for op, arg in value.items())
            else:
                ok = _compare(actual, "eq", value)
        if not ok:
            return False
    return True


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return None
        return number if math.isfinite(number) else None
    return None


def _equal(actual: Any, expected: Any) -> bool:
    if isinstance(actual, bool) or isinstance(expected, bool):
        return actual is expected if isinstance(actual, bool) and isinstance(expected, bool) else False
    a, e = _number(actual), _number(expected)
    if a is not None and e is not None and (isinstance(actual, int | float) or isinstance(expected, int | float)):
        return a == e  # "25000000" == 25000000
    return actual == expected


def _order(actual: Any, arg: Any) -> tuple[float, float] | None:
    if isinstance(actual, str) and isinstance(arg, str):
        a, e = actual.lower(), arg.lower()
        if a in SEVERITY_ORDER and e in SEVERITY_ORDER:
            return SEVERITY_ORDER[a], SEVERITY_ORDER[e]
    a, e = _number(actual), _number(arg)
    if a is None or e is None:
        return None
    return a, e


@lru_cache(maxsize=512)
def _regex(pattern: str) -> re.Pattern[str] | None:
    if len(pattern) > MAX_REGEX:
        return None
    try:
        return re.compile(pattern)
    except re.error:
        return None


def _compare(actual: Any, op: str, arg: Any) -> bool:
    if op == "exists":
        return isinstance(arg, bool) and (actual is not MISSING and actual is not None) == arg
    if actual is MISSING or actual is None:
        return False
    match op:
        case "eq":
            return _equal(actual, arg)
        case "ne":
            return not _equal(actual, arg)
        case "gt" | "gte" | "lt" | "lte":
            pair = _order(actual, arg)
            if pair is None:
                return False
            a, e = pair
            return {"gt": a > e, "gte": a >= e, "lt": a < e, "lte": a <= e}[op]
        case "in":
            return isinstance(arg, list) and any(_equal(actual, item) for item in arg)
        case "not_in":
            return isinstance(arg, list) and not any(_equal(actual, item) for item in arg)
        case "glob":
            return isinstance(actual, str) and isinstance(arg, str) and fnmatch.fnmatchcase(actual, arg)
        case "regex":
            pattern = _regex(arg) if isinstance(arg, str) else None
            return pattern is not None and isinstance(actual, str) and pattern.search(actual) is not None
        case "contains":
            if isinstance(actual, str):
                return isinstance(arg, str) and arg in actual
            if isinstance(actual, list):
                return any(_equal(item, arg) for item in actual)
            return False
    return False


# --- validation (when a rule or mapping is saved) ------------------------------------------------------


def validate_condition(cond: Any, where: str = "when") -> list[dict[str, str]]:
    """Structure check (spec §4). Returns FieldError-like dicts; never raises."""
    if not isinstance(cond, dict) or not cond:
        return [{"path": where, "message": "condition must be a non-empty object"}]
    if set(cond) >= {"field", "op"} and set(cond) <= {"field", "op", "value"}:
        return _validate_ops({cond["op"]: cond.get("value")}, where) + _validate_path(cond["field"], where)
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
            path_errors = _validate_path(key, at)
            errors += path_errors
            if not path_errors and isinstance(value, dict):
                errors += _validate_ops(value, at)
    return errors


def _validate_path(path: Any, at: str) -> list[dict[str, str]]:
    try:
        parse_path(path)
    except (PathError, TypeError) as exc:
        return [{"path": at, "message": str(exc)}]
    return []


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
        elif op == "regex" and (not isinstance(arg, str) or _regex(arg) is None):
            errors.append({"path": f"{at}.{op}", "message": f"invalid regex (max {MAX_REGEX} characters)"})
        elif op == "glob" and not isinstance(arg, str):
            errors.append({"path": f"{at}.{op}", "message": "`glob` needs a string"})
    return errors


# --- explanation (rule tester, spec §4 "Tester reguły") ------------------------------------------------


def explain(cond: Any, letter: dict[str, Any]) -> dict[str, Any]:
    """The condition tree with the result of every node (contracts/openapi.yaml `ConditionResult`)."""
    try:
        return _explain(cond, letter, 0)
    except Exception:  # noqa: BLE001 - like evaluate: never throws
        return {"op": "invalid", "result": False}


def _leaf(path: str, op: str, expected: Any, letter: dict[str, Any]) -> dict[str, Any]:
    actual = get_path(letter, path)
    node: dict[str, Any] = {"op": op, "path": path, "expected": expected, "missing": actual is MISSING}
    if actual is not MISSING:
        node["actual"] = actual
    node["result"] = _compare(actual, op, expected)
    return node


def _explain(cond: Any, letter: dict[str, Any], depth: int) -> dict[str, Any]:
    if depth > MAX_DEPTH or not isinstance(cond, dict) or not cond:
        return {"op": "invalid", "result": False}
    if set(cond) >= {"field", "op"} and set(cond) <= {"field", "op", "value"}:
        return _leaf(cond["field"], cond["op"], cond.get("value"), letter)
    nodes = []
    for key, value in cond.items():
        if key in ("all", "any"):
            children = [_explain(c, letter, depth + 1) for c in value] if isinstance(value, list) else []
            ok = all(c["result"] for c in children) if key == "all" else any(c["result"] for c in children)
            nodes.append({"op": key, "result": isinstance(value, list) and ok, "children": children})
        elif key == "not":
            child = _explain(value, letter, depth + 1)
            nodes.append({"op": "not", "result": not child["result"], "children": [child]})
        elif isinstance(value, dict) and value and set(value) <= OPERATORS:
            leaves = [_leaf(key, op, arg, letter) for op, arg in value.items()]
            if len(leaves) == 1:
                nodes.append(leaves[0])
            else:
                nodes.append({"op": "all", "result": all(n["result"] for n in leaves), "children": leaves})
        else:
            nodes.append(_leaf(key, "eq", value, letter))
    if len(nodes) == 1:
        return nodes[0]
    return {"op": "all", "result": all(n["result"] for n in nodes), "children": nodes}
