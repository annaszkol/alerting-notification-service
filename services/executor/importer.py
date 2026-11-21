"""Runbook import: original text -> canonical runbook (spec §7 table). Deterministic, no LLM.

| Format      | Import                                                                                          |
| ----------- | ----------------------------------------------------------------------------------------------- |
| `yaml`      | fields `match`/`matches`, `check`, `action`, `url`, `params`, `team`, `service`, `approval`;     |
|             | textual `if` phrases ("fails twice", "fails N times") -> `attempts` + `on_failure` (from `then`) |
|             | an unknown field or phrase -> a `text` step with `needs_review: true`                            |
| `markdown`  | every numbered item (`1.`, `2)`) -> a `text` step                                               |
| `checklist` | every `- [ ]` / `- [x]` item -> a `text` step with a checkbox                                   |
| `text`      | the whole text -> one `text` step                                                               |
| `link`      | no steps; the URL is shown on the alert                                                         |

Step ids: an explicit `id`; a `check` step -> `check-<host without -service>`; an action -> the first word of its
name (`retry_workflow` -> `retry`); duplicates get `-2`, `-3`. A fixing action (`retry_workflow`,
`restart_service`) resolves the alert on success unless `resolves` says otherwise.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

import yaml

FORMATS = ("yaml", "markdown", "checklist", "text", "link")
FIXING_ACTIONS = {"retry_workflow", "restart_service"}
KNOWN_STEP_KEYS = {
    "id", "title", "check", "action", "url", "params", "team", "service", "approval", "approval_role", "if",
    "then", "attempts", "on_failure", "resolves", "only_if_open", "wait", "duration", "text", "do",
}  # fmt: skip
NUMBER_WORDS = {"once": 1, "twice": 2, "thrice": 3, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
FAILS = re.compile(r"^\s*fails?\s+(?:(\d+)\s+times?|(\w+)(?:\s+times?)?)\s*$", re.IGNORECASE)
NUMBERED = re.compile(r"^\s*\d+[.)]\s+(.*)$")
CHECKBOX = re.compile(r"^\s*[-*]\s+\[[ xX]\]\s+(.*)$")


class ImportError_(ValueError):  # noqa: N801 - avoid shadowing the builtin ImportError
    """The original cannot be read at all (e.g. invalid YAML)."""


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "step"


def _attempts(phrase: Any) -> int | None:
    """ "fails twice" -> 2, "fails 3 times" -> 3; anything else -> None."""
    match = FAILS.match(str(phrase))
    if not match:
        return None
    if match.group(1):
        return int(match.group(1))
    return NUMBER_WORDS.get(match.group(2).lower())


def _review_step(raw: Any, why: str) -> dict[str, Any]:
    text = yaml.safe_dump(raw, sort_keys=False, allow_unicode=True).strip() if not isinstance(raw, str) else raw
    return {"kind": "text", "title": f"Review: {why}", "text": text, "needs_review": True}


def _yaml_step(raw: Mapping[str, Any]) -> tuple[dict[str, Any], str | None]:
    """One step; returns (step without id, `then` target or None)."""
    unknown = sorted(set(raw) - KNOWN_STEP_KEYS)
    if unknown:
        return _review_step(dict(raw), f"unknown field {', '.join(unknown)}"), None
    if "wait" in raw or "duration" in raw:
        return {"kind": "wait", "duration": str(raw.get("wait") or raw.get("duration"))}, None
    if "action" not in raw:
        text = raw.get("text") or raw.get("do") or raw.get("check") or raw.get("title")
        if not text:
            return _review_step(dict(raw), "step without action or text"), None
        return {"kind": "text", "text": str(text), **({"title": raw["title"]} if raw.get("title") else {})}, None

    params = dict(raw.get("params") or {})
    for key in ("url", "team", "service"):
        if key in raw:
            params[key] = raw[key]
    step: dict[str, Any] = {"kind": "action", "action": str(raw["action"]), "params": params}
    title = raw.get("title") or raw.get("check")
    if title:
        step["title"] = str(title)
    step["approval"] = "required" if str(raw.get("approval", "none")).lower() == "required" else "none"
    if raw.get("approval_role"):
        step["approval_role"] = raw["approval_role"]
    then = raw.get("then") or raw.get("on_failure")
    if "if" in raw:
        attempts = _attempts(raw["if"])
        if attempts is None:
            return _review_step(dict(raw), f"unknown condition {raw['if']!r}"), None
        step["attempts"] = attempts
    elif raw.get("attempts"):
        step["attempts"] = int(raw["attempts"])
    if "resolves" in raw:
        step["resolves"] = bool(raw["resolves"])
    elif step["action"] in FIXING_ACTIONS:
        step["resolves"] = True
    if raw.get("only_if_open"):
        step["only_if_open"] = True
    return step, (str(then) if then else None)


def _step_id(raw: Mapping[str, Any], step: Mapping[str, Any], taken: set[str]) -> str:
    if raw.get("id"):
        base = _slug(str(raw["id"]))
    elif step["kind"] == "action" and raw.get("check") and step["params"].get("url"):
        host = urlsplit(str(step["params"]["url"])).hostname or "service"
        base = "check-" + _slug(host.split(".")[0].removesuffix("-service"))
    elif step["kind"] == "action":
        base = _slug(step["action"].split("_")[0])
    elif step["kind"] == "wait":
        base = "wait"
    else:
        base = "review" if step.get("needs_review") else "step"
    candidate, n = base, 1
    while candidate in taken:
        n += 1
        candidate = f"{base}-{n}"
    taken.add(candidate)
    return candidate


def _from_yaml(original: str) -> dict[str, Any]:
    try:
        doc = yaml.safe_load(original)
    except yaml.YAMLError as exc:
        raise ImportError_(f"invalid YAML: {exc}") from exc
    if isinstance(doc, list):
        doc = {"steps": doc}
    if not isinstance(doc, Mapping):
        raise ImportError_("YAML runbook must be a mapping with `steps` (or a list of steps)")
    out: dict[str, Any] = {}
    match = doc.get("match") or doc.get("matches")
    if isinstance(match, Mapping):
        out["match"] = dict(match)
    if doc.get("title"):
        out["title"] = str(doc["title"])
    steps: list[dict[str, Any]] = []
    targets: list[str | None] = []
    raws: list[Any] = []
    taken: set[str] = set()
    for raw in doc.get("steps") or []:
        if isinstance(raw, Mapping):
            step, then = _yaml_step(raw)
        else:
            step, then = {"kind": "text", "text": str(raw)}, None
        step = {"id": _step_id(raw if isinstance(raw, Mapping) else {}, step, taken), **step}
        steps.append(step)
        targets.append(then)
        raws.append(raw)
    # `then: escalate` names a step id or the action of a later step
    by_action: dict[str, str] = {}
    for step in steps:
        if step["kind"] == "action":
            by_action.setdefault(step["action"], step["id"])
    ids = {s["id"] for s in steps}
    for step, then in zip(steps, targets, strict=True):
        if not then:
            continue
        target = then if then in ids else by_action.get(then)
        if target:
            step["on_failure"] = target
        else:
            step["needs_review"] = True
    out["steps"] = steps
    return out


def _numbered(original: str) -> list[dict[str, Any]]:
    items: list[str] = []
    for line in original.splitlines():
        match = NUMBERED.match(line)
        if match:
            items.append(match.group(1).strip())
        elif items and line.strip() and line[:1].isspace():
            items[-1] += " " + line.strip()  # continuation of the previous item
    return [{"id": f"step-{i}", "kind": "text", "text": text} for i, text in enumerate(items, 1)]


def _checklist(original: str) -> list[dict[str, Any]]:
    items = [m.group(1).strip() for line in original.splitlines() if (m := CHECKBOX.match(line))]
    return [{"id": f"item-{i}", "kind": "text", "checkbox": True, "text": text} for i, text in enumerate(items, 1)]


def import_runbook(
    runbook_id: str, owner_team: str, fmt: str, original: str, title: str | None = None
) -> dict[str, Any]:
    """Canonical runbook (contracts/runbook.schema.json). Raises ImportError_ only for unreadable YAML."""
    if fmt not in FORMATS:
        raise ImportError_(f"unknown format {fmt!r}")
    canonical: dict[str, Any] = {"id": runbook_id, "owner": {"team": owner_team}, "format": fmt, "mode": "manual"}
    if fmt == "yaml":
        canonical |= _from_yaml(original)
    elif fmt == "markdown":
        canonical["steps"] = _numbered(original) or [{"id": "step-1", "kind": "text", "text": original.strip()}]
    elif fmt == "checklist":
        canonical["steps"] = _checklist(original) or [{"id": "step-1", "kind": "text", "text": original.strip()}]
    elif fmt == "text":
        canonical["steps"] = [{"id": "step-1", "kind": "text", "text": original.strip()}] if original.strip() else []
    else:  # link
        canonical["link"] = original.strip()
        canonical["steps"] = []
    if title:
        canonical["title"] = title
    return canonical
