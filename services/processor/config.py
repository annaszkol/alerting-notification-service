"""Runtime configuration of the processor: rules, mappings, sources (spec §4 "Zmiany w runtime", §8).

Loaded from Postgres and kept in memory; reloaded when `config_version` changes (the processor checks on
`NOTIFY config_changed` and every 5 s). Tables are A's (spec §8 names); a table that does not exist yet counts as
empty, so the processor starts before every migration has landed.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import psycopg
from psycopg.rows import dict_row

from libs.conditions import SchemaMapping, Source

log = logging.getLogger("processor.config")


@dataclass(frozen=True)
class Rule:
    id: str
    owner: Mapping[str, str]
    when: Mapping[str, Any]
    route: list[Any]
    present: Mapping[str, Any]
    dedup: Mapping[str, Any]
    auto_resolve_after: timedelta | None
    runbook_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class Config:
    version: int = 0
    rules: tuple[Rule, ...] = ()
    mappings: tuple[SchemaMapping, ...] = ()
    sources: Mapping[str, Source] = field(default_factory=dict)

    def source(self, source_id: str) -> Source:
        return self.sources.get(source_id) or Source(id=source_id)


def _exists(conn: psycopg.Connection, table: str) -> bool:
    return conn.execute("SELECT to_regclass(%s) IS NOT NULL", (table,)).fetchone()[0]


# The processor writes these; without them it must not consume (offsets would move past unprocessed events).
REQUIRED_TABLES = ("processed_events", "alerts")


def tables_ready(conn: psycopg.Connection) -> bool:
    return all(_exists(conn, table) for table in REQUIRED_TABLES)


def current_version(conn: psycopg.Connection) -> int:
    if not _exists(conn, "config_version"):
        return 0
    row = conn.execute("SELECT max(version) FROM config_version").fetchone()
    return int(row[0] or 0)


def load(conn: psycopg.Connection) -> Config:
    version = current_version(conn)
    rules: list[Rule] = []
    mappings: list[SchemaMapping] = []
    sources: dict[str, Source] = {}
    with conn.cursor(row_factory=dict_row) as cur:
        if _exists(conn, "rules"):
            for r in cur.execute("SELECT * FROM rules WHERE enabled ORDER BY id").fetchall():
                rules.append(
                    Rule(
                        id=r["id"],
                        owner=r["owner"],
                        when=r["when"],
                        route=list(r.get("route") or []),
                        present=r.get("present") or {},
                        dedup=r.get("dedup") or {},
                        auto_resolve_after=r.get("auto_resolve_after"),
                        runbook_ids=tuple(r.get("runbook_ids") or ()),
                    )
                )
        if _exists(conn, "schema_mappings"):
            for m in cur.execute("SELECT * FROM schema_mappings WHERE enabled").fetchall():
                mappings.append(
                    SchemaMapping(
                        name=m["id"],
                        fields=m["fields"] or {},
                        priority=m.get("priority") or 100,
                        sources=tuple(m["source_ids"]) if m.get("source_ids") else None,
                        detect=m.get("detect"),
                        schema_from=m.get("schema_from"),
                        split_on=m.get("split_on"),
                    )
                )
        if _exists(conn, "sources"):
            for s in cur.execute("SELECT * FROM sources WHERE enabled").fetchall():
                parser = s.get("parser") or "json"
                parser_config: dict[str, Any] = {}
                if isinstance(parser, dict):  # {"type": "regex", "pattern": "..."}
                    parser_config, parser = parser, parser.get("type", "json")
                sources[s["id"]] = Source(
                    id=s["id"],
                    type=s.get("type") or "http",
                    parser=parser,
                    parser_config=parser_config,
                    default_severity=s.get("default_severity") or "info",
                )
    log.info("config v%s: %d rules, %d mappings, %d sources", version, len(rules), len(mappings), len(sources))
    return Config(version, tuple(rules), tuple(mappings), sources)
