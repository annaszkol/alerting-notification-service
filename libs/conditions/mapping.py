"""Event -> letter (spec §3 "Letter i mapowania").

1. Parse the payload with the source's parser: JSON (default), `logfmt` or `regex` with named groups.
2. Detect: mappings in priority order (lower number first, then name); optional `sources` list and `detect`
   condition (evaluated against the parsed payload). The first match wins.
3. Split: `splitOn: alerts` turns the array at that path into one letter per element; if the path is absent the
   payload is one event (Alertmanager sends `{"alerts": [...]}`, the sample is a single alert: both work).
4. No match: the letter still goes on with `$schema = "unmapped"` and the defaults; the processor keeps a sample.

A field value in a mapping is a path (`labels.service`), `{const: v}`, a template string (`"{{a}}: {{b}}"`), or
`{from: path, map: {...}}` / `{from: path, format: iso8601 | epoch_ms | epoch_s}`.

Letter = `{"$eventId", "$sourceId", "$sourceType", "$receivedAt", "$schema", "$service", "$instance",
"$environment", "$severity", "$severityRaw", "$status", "$eventTime", "$summary", "$correlationKey", ...,
"payload": <parsed element>}`.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from libs.conditions.evaluate import evaluate
from libs.conditions.paths import MISSING, get_in
from libs.conditions.templates import PLACEHOLDER, render

SEVERITIES = ("info", "warning", "error", "critical")
UNMAPPED = "unmapped"


@dataclass(frozen=True)
class Source:
    id: str
    type: str = "http"  # http | kafka | logfile
    parser: str = "json"  # json | logfmt | regex
    parser_config: Mapping[str, Any] = field(default_factory=dict)  # {"pattern": "..."} for regex
    default_severity: str = "info"


@dataclass(frozen=True)
class SchemaMapping:
    name: str
    fields: Mapping[str, Any]
    priority: int = 100
    sources: tuple[str, ...] | None = None
    detect: Mapping[str, Any] | None = None
    schema_from: str | None = None
    split_on: str | None = None

    @classmethod
    def from_doc(cls, doc: Mapping[str, Any]) -> SchemaMapping:
        """From the YAML / JSON shape of spec §3 (`splitOn`, `schemaFrom` or snake_case)."""
        sources = doc.get("sources") or doc.get("source_ids")
        return cls(
            name=doc["name"] if "name" in doc else doc["id"],
            fields=doc.get("fields", {}),
            priority=int(doc.get("priority", 100)),
            sources=tuple(sources) if sources else None,
            detect=doc.get("detect"),
            schema_from=doc.get("schemaFrom") or doc.get("schema_from"),
            split_on=doc.get("splitOn") or doc.get("split_on"),
        )


@dataclass(frozen=True)
class Envelope:
    """contracts/event.py EventEnvelope, as the processor reads it from Kafka."""

    event_id: str
    source_id: str
    received_at: datetime
    content_type: str
    payload: Any


@dataclass(frozen=True)
class Mapped:
    letter: dict[str, Any]
    mapping: str | None  # None = unmapped


# --- parsing -------------------------------------------------------------------------------------------

_LOGFMT = re.compile(r'(\w[\w.\-]*)=("(?:[^"\\]|\\.)*"|\S*)')


def parse_logfmt(line: str) -> dict[str, str]:
    """`ts=... level=WARN msg="quote latency high"` -> dict (spec §3 pricing-engine log format)."""
    out: dict[str, str] = {}
    for key, raw in _LOGFMT.findall(line):
        if raw.startswith('"'):
            try:
                raw = shlex.split(raw)[0]
            except ValueError:
                raw = raw.strip('"')
        out[key] = raw
    return out


def parse_payload(payload: Any, content_type: str, source: Source) -> Any:
    if not isinstance(payload, str | bytes):
        return payload  # already decoded JSON
    text = payload.decode("utf-8", "replace") if isinstance(payload, bytes) else payload
    if source.parser == "logfmt" or content_type == "text/logfmt":
        return parse_logfmt(text)
    if source.parser == "regex":
        pattern = source.parser_config.get("pattern")
        match = re.search(pattern, text) if isinstance(pattern, str) else None
        return match.groupdict() if match else {"line": text}
    try:
        return json.loads(text)
    except ValueError:
        return {"line": text}


# --- mapping -------------------------------------------------------------------------------------------


def _iso(value: Any, fmt: str | None) -> str | None:
    try:
        if fmt == "epoch_ms" or (fmt is None and isinstance(value, int | float) and value > 10**11):
            return datetime.fromtimestamp(float(value) / 1000, UTC).isoformat()
        if fmt == "epoch_s" or (fmt is None and isinstance(value, int | float)):
            return datetime.fromtimestamp(float(value), UTC).isoformat()
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).astimezone(UTC).isoformat()
    except (ValueError, OverflowError, OSError, TypeError):
        return None


def _value(spec: Any, payload: Any, letter: dict[str, Any]) -> Any:
    """One mapping value: const, template, path, or {from, map | format}. MISSING when absent."""
    if isinstance(spec, Mapping):
        if "const" in spec:
            return spec["const"]
        raw = get_in(payload, spec.get("from", ""))
        if raw is MISSING or raw is None:
            return MISSING
        if "map" in spec:
            table = spec["map"] or {}
            return table.get(raw, table.get(str(raw), MISSING))
        if "format" in spec:
            return _iso(raw, spec["format"]) or MISSING
        return raw
    if isinstance(spec, str):
        if PLACEHOLDER.search(spec):
            return render(spec, {**letter, "payload": payload})
        return get_in(payload, spec)
    return MISSING


def _summary(payload: Any) -> str:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, default=str)
    return text[:200]


def build_letter(
    envelope: Envelope, source: Source, payload: Any, mapping: SchemaMapping | None, index: int | None
) -> dict[str, Any]:
    received = envelope.received_at.astimezone(UTC).isoformat()
    letter: dict[str, Any] = {
        "$eventId": envelope.event_id if index is None else f"{envelope.event_id}#{index}",
        "$sourceId": envelope.source_id,
        "$sourceType": source.type,
        "$receivedAt": received,
        "$schema": UNMAPPED,
    }
    if mapping is not None:
        letter["$schema"] = mapping.name
        if mapping.schema_from:
            schema = get_in(payload, mapping.schema_from)
            if isinstance(schema, str | int | float) and str(schema):
                letter["$schema"] = str(schema)

        # Paths and constants first, templates last, so a template like $summary can use mapped `$` fields.
        def is_template(item: tuple[str, Any]) -> bool:
            return isinstance(item[1], str) and bool(PLACEHOLDER.search(item[1]))

        ordered = sorted(mapping.fields.items(), key=is_template)
        for name, spec in ordered:
            if not name.startswith("$"):
                continue
            value = _value(spec, payload, letter)
            if value is not MISSING:
                letter[name] = value
                if name == "$severity" and isinstance(spec, Mapping) and "from" in spec:
                    letter["$severityRaw"] = get_in(payload, spec["from"])

    severity = str(letter.get("$severity", "")).lower()
    letter["$severity"] = severity if severity in SEVERITIES else source.default_severity
    status = str(letter.get("$status", "firing")).lower()
    letter["$status"] = status if status in ("firing", "resolved") else "firing"
    event_time = letter.get("$eventTime")
    letter["$eventTime"] = (_iso(event_time, None) if event_time is not None else None) or received
    letter.setdefault("$summary", _summary(payload))
    for name in ("$service", "$instance", "$environment", "$correlationKey"):
        letter.setdefault(name, None)
    letter["payload"] = payload
    return letter


def _ordered(mappings: Iterable[SchemaMapping]) -> list[SchemaMapping]:
    return sorted(mappings, key=lambda m: (m.priority, m.name))


def detect(payload: Any, source: Source, mappings: Iterable[SchemaMapping]) -> SchemaMapping | None:
    view = {"payload": payload, "$sourceId": source.id, "$sourceType": source.type}
    for mapping in _ordered(mappings):
        if mapping.sources and source.id not in mapping.sources:
            continue
        if mapping.detect is None or evaluate(mapping.detect, view):
            return mapping
    return None


def map_event(envelope: Envelope, source: Source, mappings: Iterable[SchemaMapping]) -> list[Mapped]:
    """Letters for one envelope (several when the mapping splits). Never raises for bad payloads."""
    payload = parse_payload(envelope.payload, envelope.content_type, source)
    mapping = detect(payload, source, mappings)
    if mapping is not None and mapping.split_on:
        items = get_in(payload, mapping.split_on)
        if isinstance(items, list):
            return [
                Mapped(build_letter(envelope, source, item, mapping, i), mapping.name) for i, item in enumerate(items)
            ]
    return [Mapped(build_letter(envelope, source, payload, mapping, None), mapping.name if mapping else None)]


def shape_hash(payload: Any) -> str:
    """Groups unmapped samples by their set of keys (spec §3 "grupowanie po zbiorze kluczy")."""

    def keys(node: Any, prefix: str = "") -> Iterable[str]:
        if isinstance(node, dict):
            for key, value in node.items():
                path = f"{prefix}.{key}" if prefix else str(key)
                yield path
                yield from keys(value, path)

    return hashlib.sha256("\n".join(sorted(set(keys(payload)))).encode()).hexdigest()[:16]
