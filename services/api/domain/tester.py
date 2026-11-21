"""Rule tester (spec §4 "Tester reguły w UI"): paste or pick an event and see the letter, the result of every
condition, the rendered title, the fingerprint and the actual recipients and channels right now. Writes nothing.

Uses exactly the code the pipeline runs: `libs.conditions` mapping and evaluation, the processor's fingerprint and
B's `plan_targets`, so the tester and the processor cannot disagree.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import psycopg

from db.rows import plain_rows
from libs.conditions import MISSING, Envelope, explain, get_path, map_event, render
from services.api.durations import parse_duration
from services.api.errors import not_found
from services.delivery.directory import SqlDirectory
from services.delivery.planning import plan_targets
from services.processor import config as processor_config
from services.processor.batch import fingerprint
from services.processor.config import Rule

SEVERITIES = ("info", "warning", "error", "critical")


def _rule(doc: Mapping[str, Any]) -> Rule:
    after = doc.get("autoResolveAfter")
    return Rule(
        id=doc["id"],
        owner=doc["owner"],
        when=doc["when"],
        route=list(doc.get("route") or []),
        present=doc.get("present") or {},
        dedup=doc.get("dedup") or {},
        auto_resolve_after=parse_duration(after) if after else None,
        runbook_ids=tuple(doc.get("runbooks") or ()),
    )


def _describe(entry: Mapping[str, Any]) -> str:
    to = entry.get("to", {})
    who = ", ".join(f"{k}: {v}" for k, v in to.items())
    channels = entry.get("channels", "preferences")
    if isinstance(channels, Mapping):
        windows = ", ".join(w["between"] for w in channels.get("schedule", []))
        how = f"schedule {windows}"
    elif isinstance(channels, list):
        how = "channels from the rule"
    else:
        how = "personal preferences"
    return f"{who} ({how})"


@plain_rows
def _letters(conn: psycopg.Connection, event: Mapping[str, Any] | None, alert_id: int | None, now: datetime):
    if alert_id is not None:
        row = conn.execute("SELECT first_event FROM alerts WHERE id = %s", (alert_id,)).fetchone()
        if row is None:
            raise not_found(f"alert {alert_id}")
        return [(row[0], None)]
    config = processor_config.load(conn)
    source_id = event.get("sourceId") or "http-default"
    envelope = Envelope(
        f"test-{uuid.uuid4()}", source_id, now, event.get("contentType") or "application/json", event["payload"]
    )
    return [(m.letter, m.mapping) for m in map_event(envelope, config.source(source_id), config.mappings)]


@plain_rows
def _recipients(conn: psycopg.Connection, rule: Rule, severity: str, now: datetime) -> list[dict[str, Any]]:
    """Per person: channels and why (which route entry)."""
    by_user: dict[str, dict[str, Any]] = {}
    for entry in rule.route:
        for user, channel in plan_targets(conn, SqlDirectory(), [entry], now, severity, rule.owner.get("team")):
            person = by_user.setdefault(user, {"userId": user, "channels": [], "reasons": []})
            if channel.value not in person["channels"]:
                person["channels"].append(channel.value)
            if _describe(entry) not in person["reasons"]:
                person["reasons"].append(_describe(entry))
    return [
        {"userId": p["userId"], "channels": sorted(p["channels"]), "reason": "; ".join(p["reasons"])}
        for p in sorted(by_user.values(), key=lambda p: p["userId"])
    ]


def test_rule(
    conn: psycopg.Connection,
    doc: Mapping[str, Any],
    event: Mapping[str, Any] | None,
    alert_id: int | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    rule = _rule(doc)
    present = rule.present
    out = []
    for letter, mapping in _letters(conn, event, alert_id, now):
        tree = explain(rule.when, letter)
        matched = tree["result"]
        severity = str(present.get("severity") or letter.get("$severity") or "info").lower()
        severity = severity if severity in SEVERITIES else "info"
        fields = []
        for path in present.get("fields") or []:
            value = get_path(letter, path)
            fields.append({"path": path, "value": None if value is MISSING else value})
        out.append(
            {
                "letter": letter,
                "mappingName": mapping,
                "matched": matched,
                "conditions": tree,
                "title": render(present.get("title") or "{{$summary}}", letter),
                "body": render(present.get("body"), letter) if present.get("body") else None,
                "fields": fields,
                "fingerprint": fingerprint(rule, letter) if matched else None,
                "recipients": _recipients(conn, rule, severity, now) if matched else [],
            }
        )
    return {"letters": out}
