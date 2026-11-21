"""Load demo people / teams / on-call / preferences into Postgres (owner B).

Usage: DATABASE_URL=... python -m deploy.seed.load [path ...] [--reset]

Without --reset existing rows are kept (INSERT ... ON CONFLICT DO NOTHING), so changes made through the UI
survive `docker compose up`. --reset overwrites rows that are in the file (rows not in the file stay).
Everything runs in one transaction: a broken file changes nothing.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from collections.abc import Mapping
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import psycopg
import yaml
from psycopg.types.json import Jsonb

log = logging.getLogger("seed")

SEED_DIR = Path(__file__).parent
DEFAULT_FILES = [SEED_DIR / "people.yaml", SEED_DIR / "runbooks.yaml", SEED_DIR / "config.yaml"]
DEV_INGEST_TOKEN = "dev-ingest-token"  # local demo default; set SEED_INGEST_TOKEN outside demos

SLUG = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
DURATION = re.compile(r"^(\d+)([smhdw])$")
WINDOW = re.compile(r"^([01]\d|2[0-3]):[0-5]\d-([01]\d|2[0-3]):[0-5]\d$")
CHANNELS = {"ui", "email", "sms", "phone"}
SEVERITIES = {"info", "warning", "error", "critical"}
ROLES = {"manager", "member"}
UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days", "w": "weeks"}


class SeedError(Exception):
    pass


def parse_duration(value: str) -> timedelta:
    match = DURATION.match(str(value))
    if not match:
        raise SeedError(f"bad duration {value!r}, expected e.g. 30m, 12h, 1d, 7d")
    return timedelta(**{UNITS[match[2]]: int(match[1])})


def load_files(paths: list[Path]) -> dict[str, Any]:
    merged: dict[str, Any] = {
        "users": [], "teams": [], "preferences": {}, "runbooks": [], "sources": [], "mappings": [], "rules": []
    }  # fmt: skip
    for path in paths:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        merged["users"] += data.get("users", [])
        merged["teams"] += data.get("teams", [])
        merged["preferences"].update(data.get("preferences", {}))
        for key in ("runbooks", "sources", "mappings", "rules"):
            merged[key] += data.get(key, [])
    return merged


def _check_channel_spec(spec: Any, where: str, errors: list[str]) -> None:
    if isinstance(spec, list):
        bad = set(spec) - CHANNELS
        if bad:
            errors.append(f"{where}: unknown channels {sorted(bad)}")
    elif isinstance(spec, Mapping) and "schedule" in spec:
        for i, window in enumerate(spec["schedule"]):
            if not WINDOW.match(str(window.get("between", ""))):
                errors.append(f"{where}.schedule[{i}]: bad window {window.get('between')!r}")
            if any(d not in range(1, 8) for d in window.get("days", [])):
                errors.append(f"{where}.schedule[{i}]: days must be 1..7")
            _check_channel_spec(window.get("use", []), f"{where}.schedule[{i}].use", errors)
        _check_channel_spec(spec.get("otherwise", []), f"{where}.otherwise", errors)
    else:
        errors.append(f"{where}: expected a channel list or a schedule")


def _check_preferences(prefs: Mapping[str, Any], where: str, errors: list[str]) -> None:
    for severity, spec in prefs.get("by_severity", {}).items():
        if severity not in SEVERITIES:
            errors.append(f"{where}: unknown severity {severity!r}")
        _check_channel_spec(spec, f"{where}.{severity}", errors)
    for channel, limit in prefs.get("limits", {}).items():
        if channel not in CHANNELS:
            errors.append(f"{where}.limits: unknown channel {channel!r}")
        try:
            if int(limit["max"]) < 1:
                raise ValueError
            parse_duration(limit["window"])
        except (KeyError, ValueError, TypeError, SeedError):
            errors.append(f"{where}.limits.{channel}: expected {{max: >=1, window: e.g. 10m}}")


def validate(data: Mapping[str, Any]) -> list[str]:
    """All problems in the seed data (empty list = valid)."""
    errors: list[str] = []
    users = [u.get("id") for u in data["users"]]
    known = set(users)
    for uid in {u for u in users if users.count(u) > 1}:
        errors.append(f"user {uid!r} defined twice")
    for user in data["users"]:
        if not SLUG.match(str(user.get("id", ""))):
            errors.append(f"user id {user.get('id')!r} is not a slug")
        if not user.get("name"):
            errors.append(f"user {user.get('id')!r} has no name")
    phones = [u["phone"] for u in data["users"] if u.get("phone")]
    for phone in {p for p in phones if phones.count(p) > 1}:
        errors.append(f"phone {phone} used twice")

    team_ids = [t.get("id") for t in data["teams"]]
    for tid in {t for t in team_ids if team_ids.count(t) > 1}:
        errors.append(f"team {tid!r} defined twice")
    for team in data["teams"]:
        tid = team.get("id")
        if not SLUG.match(str(tid or "")):
            errors.append(f"team id {tid!r} is not a slug")
        members = team.get("members", {})
        for user, role in members.items():
            if user not in known:
                errors.append(f"team {tid}: unknown member {user!r}")
            if role not in ROLES:
                errors.append(f"team {tid}: {user} has unknown role {role!r}")
        oncall = team.get("oncall")
        if oncall:
            rotation = oncall.get("rotation", [])
            if not rotation:
                errors.append(f"team {tid}: empty on-call rotation")
            for user in rotation:
                if user not in members:
                    errors.append(f"team {tid}: on-call {user!r} is not a team member")
            try:
                parse_duration(oncall.get("shift_length", "1d"))
            except SeedError as exc:
                errors.append(f"team {tid}: {exc}")
        if "preferences" in team:
            _check_preferences(team["preferences"], f"team {tid}", errors)

    for user, prefs in data["preferences"].items():
        if user not in known:
            errors.append(f"preferences for unknown user {user!r}")
        _check_preferences(prefs, f"user {user}", errors)
    _check_runbooks(data, errors)
    _check_config(data, errors)
    return errors


def _check_config(data: Mapping[str, Any], errors: list[str]) -> None:
    from libs.conditions import validate_condition, validate_template

    users = {u.get("id") for u in data["users"]}
    teams = {t.get("id") for t in data["teams"]}
    runbooks = {r.get("id") for r in data.get("runbooks", [])}
    sources = {s.get("id") for s in data.get("sources", [])}
    for source in data.get("sources", []):
        if source.get("type") not in ("http", "kafka", "logfile"):
            errors.append(f"source {source.get('id')}: unknown type {source.get('type')!r}")
        if source.get("parser", "json") not in ("json", "logfmt", "regex"):
            errors.append(f"source {source.get('id')}: unknown parser {source.get('parser')!r}")
    for mapping in data.get("mappings", []):
        name = mapping.get("name")
        if mapping.get("detect") is not None:
            detect_errors = validate_condition(mapping["detect"], "detect")
            errors += [f"mapping {name}: {e['path']}: {e['message']}" for e in detect_errors]
        for source in mapping.get("sources") or []:
            if source not in sources:
                errors.append(f"mapping {name}: unknown source {source!r}")
    for rule in data.get("rules", []):
        rid, owner = rule.get("id"), rule.get("owner") or {}
        if ("team" in owner and owner["team"] not in teams) or ("user" in owner and owner["user"] not in users):
            errors.append(f"rule {rid}: unknown owner {owner}")
        errors += [f"rule {rid}: {e['path']}: {e['message']}" for e in validate_condition(rule.get("when"))]
        for key in ("title", "body"):
            if key in (rule.get("present") or {}):
                errors += [f"rule {rid}: {e['message']}" for e in validate_template(rule["present"][key], key)]
        for runbook in rule.get("runbooks", []):
            if runbook not in runbooks:
                errors.append(f"rule {rid}: unknown runbook {runbook!r}")


def _check_runbooks(data: Mapping[str, Any], errors: list[str]) -> None:
    from services.executor.runbooks import validate as validate_runbook

    teams = {t.get("id"): t.get("members", {}) for t in data["teams"]}
    for runbook in data.get("runbooks", []):
        rid, owner = runbook.get("id"), runbook.get("owner")
        canonical = runbook.get("canonical") or {}
        if canonical.get("id") != rid:
            errors.append(f"runbook {rid}: canonical.id must be {rid!r}")
        if owner not in teams:
            errors.append(f"runbook {rid}: unknown owner team {owner!r}")
        elif runbook.get("approved_by") and teams[owner].get(runbook["approved_by"]) != "manager":
            errors.append(f"runbook {rid}: approved_by must be a manager of {owner}")
        for err in validate_runbook(canonical):
            errors.append(f"runbook {rid}: {err['path']}: {err['message']}")


UPSERTS = {
    "users": (
        "INSERT INTO users (id, name, email, phone, timezone, is_admin)"
        " VALUES (%(id)s, %(name)s, %(email)s, %(phone)s, %(timezone)s, %(is_admin)s) ON CONFLICT (id)",
        "name = EXCLUDED.name, email = EXCLUDED.email, phone = EXCLUDED.phone,"
        " timezone = EXCLUDED.timezone, is_admin = EXCLUDED.is_admin",
    ),
    "teams": (
        "INSERT INTO teams (id, name) VALUES (%(id)s, %(name)s) ON CONFLICT (id)",
        "name = EXCLUDED.name",
    ),
    "team_members": (
        "INSERT INTO team_members (team_id, user_id, role) VALUES (%(team)s, %(user)s, %(role)s)"
        " ON CONFLICT (team_id, user_id)",
        "role = EXCLUDED.role",
    ),
    "oncall_schedules": (
        "INSERT INTO oncall_schedules (team_id, rotation, shift_length, handoff_time, timezone, starts_on)"
        " VALUES (%(team)s, %(rotation)s, %(shift)s, %(handoff)s, %(tz)s, %(starts)s) ON CONFLICT (team_id)",
        "rotation = EXCLUDED.rotation, shift_length = EXCLUDED.shift_length, handoff_time = EXCLUDED.handoff_time,"
        " timezone = EXCLUDED.timezone, starts_on = EXCLUDED.starts_on, updated_at = now()",
    ),
    "channel_preferences": (
        "INSERT INTO channel_preferences (owner_type, owner_id, by_severity, limits)"
        " VALUES (%(type)s, %(id)s, %(by)s, %(limits)s) ON CONFLICT (owner_type, owner_id)",
        "by_severity = EXCLUDED.by_severity, limits = EXCLUDED.limits, updated_at = now()",
    ),
}


def apply(conn: psycopg.Connection, data: Mapping[str, Any], reset: bool = False) -> dict[str, int]:
    """Insert (or with reset: upsert) everything in the caller's transaction. Returns rows written per table."""
    written = dict.fromkeys([*UPSERTS, "runbooks"], 0)

    def write(table: str, params: Mapping[str, Any]) -> None:
        insert, update = UPSERTS[table]
        sql = f"{insert} DO UPDATE SET {update}" if reset else f"{insert} DO NOTHING"
        written[table] += conn.execute(sql, params).rowcount

    for user in data["users"]:
        write(
            "users",
            {
                "id": user["id"],
                "name": user["name"],
                "email": user.get("email"),
                "phone": user.get("phone"),
                "timezone": user.get("timezone", "UTC"),
                "is_admin": bool(user.get("is_admin", False)),
            },
        )
    for team in data["teams"]:
        write("teams", {"id": team["id"], "name": team.get("name", team["id"])})
        for user, role in team.get("members", {}).items():
            write("team_members", {"team": team["id"], "user": user, "role": role})
        if oncall := team.get("oncall"):
            write(
                "oncall_schedules",
                {
                    "team": team["id"],
                    "rotation": list(oncall["rotation"]),
                    "shift": parse_duration(oncall.get("shift_length", "1d")),
                    "handoff": oncall.get("handoff_time", "09:00"),
                    "tz": oncall.get("timezone", "UTC"),
                    "starts": oncall.get("starts_on") or date.today(),
                },
            )

    prefs = [("team", t["id"], t["preferences"]) for t in data["teams"] if "preferences" in t]
    prefs += [("user", user, p) for user, p in data["preferences"].items()]
    for owner_type, owner_id, p in prefs:
        write(
            "channel_preferences",
            {
                "type": owner_type,
                "id": owner_id,
                "by": Jsonb(p.get("by_severity", {})),
                "limits": Jsonb(p.get("limits", {})),
            },
        )
    from services.executor.runbooks import upsert as upsert_runbook

    for runbook in data.get("runbooks", []):
        written["runbooks"] += upsert_runbook(
            conn,
            runbook["canonical"],
            owner_team=runbook["owner"],
            title=runbook.get("title", runbook["id"]),
            fmt=runbook.get("format", "text"),
            original=runbook.get("original", ""),
            approved_by=runbook.get("approved_by"),
            reset=reset,
        )
    written.update(_apply_config(conn, data, reset))
    if written.get("sources") or written.get("mappings") or written.get("rules"):
        from contracts.notify import CONFIG_CHANGED, config_changed_payload

        version = conn.execute("UPDATE config_version SET version = version + 1 RETURNING version").fetchone()[0]
        conn.execute("SELECT pg_notify(%s, %s)", (CONFIG_CHANGED, config_changed_payload(version)))
    return written


def _apply_config(conn: psycopg.Connection, data: Mapping[str, Any], reset: bool) -> dict[str, int]:
    import hashlib

    token = os.environ.get("SEED_INGEST_TOKEN") or DEV_INGEST_TOKEN
    token_sha = hashlib.sha256(token.encode()).hexdigest()
    counts = {"sources": 0, "mappings": 0, "rules": 0}

    def write(table: str, key: str, columns: dict[str, Any]) -> None:
        names = ", ".join(f'"{c}"' for c in columns)
        values = ", ".join(f"%({c})s" for c in columns)
        update = ", ".join(f'"{c}" = EXCLUDED."{c}"' for c in columns if c != "id")
        conflict = f"DO UPDATE SET {update}" if reset else "DO NOTHING"
        sql = f"INSERT INTO {table} ({names}) VALUES ({values}) ON CONFLICT (id) {conflict}"
        counts[key] += conn.execute(sql, columns).rowcount

    for s in data.get("sources", []):
        write("sources", "sources", {
            "id": s["id"], "type": s["type"], "config": Jsonb(s.get("config", {})), "parser": s.get("parser", "json"),
            "parser_config": Jsonb(s.get("parser_config", {})), "default_severity": s.get("default_severity", "info"),
            "token_sha256": token_sha if s["type"] == "http" else None, "updated_by": "seed",
        })  # fmt: skip
    for m in data.get("mappings", []):
        write("schema_mappings", "mappings", {
            "id": m["name"], "priority": m.get("priority", 100), "source_ids": m.get("sources"),
            "detect": Jsonb(m.get("detect")), "schema_from": m.get("schemaFrom"), "split_on": m.get("splitOn"),
            "fields": Jsonb(m.get("fields", {})), "updated_by": "seed",
        })  # fmt: skip
    for r in data.get("rules", []):
        after = r.get("autoResolveAfter")
        write("rules", "rules", {
            "id": r["id"], "owner": Jsonb(r["owner"]), "when": Jsonb(r["when"]), "route": Jsonb(r.get("route", [])),
            "present": Jsonb(r.get("present", {})), "dedup": Jsonb(r.get("dedup", {})),
            "auto_resolve_after": parse_duration(after) if after else None, "runbook_ids": r.get("runbooks", []),
            "updated_by": "seed",
        })  # fmt: skip
    return counts


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="*", type=Path, default=DEFAULT_FILES)
    parser.add_argument("--reset", action="store_true", help="overwrite existing rows with the file's values")
    args = parser.parse_args(argv)

    conninfo = os.environ.get("DATABASE_URL")
    if not conninfo:
        log.error("DATABASE_URL is not set")
        return 2
    data = load_files(args.files)
    errors = validate(data)
    if errors:
        for error in errors:
            log.error("%s", error)
        return 1
    with psycopg.connect(conninfo) as conn:  # one transaction, committed on exit
        written = apply(conn, data, reset=args.reset)
    log.info("seed done (%s): %s", "reset" if args.reset else "insert missing", written)
    return 0


if __name__ == "__main__":
    sys.exit(main())
