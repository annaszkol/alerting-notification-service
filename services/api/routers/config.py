"""Configuration endpoints (contracts/openapi.yaml tags mappings, preferences, oncall, suggestions, admin;
spec §9 P2 / P3). Built by B while C is out of capacity.

Permissions: mappings, unmapped samples, sources, users, teams: admin. Preferences: the user. On-call: read for
everyone, change by a manager of the team. Suggestions: anyone proposes, a manager of the target's team decides.
Every change of mappings or sources bumps `config_version` (processors reload), like rules do.
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Mapping
from datetime import UTC, date, datetime, time
from typing import Annotated, Any, Literal

import psycopg
from fastapi import APIRouter, Body, Depends, Query, Response
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from libs.conditions import (
    Envelope,
    PathError,
    SchemaMapping,
    Source,
    map_event,
    parse_path,
    shape_keys,
    validate_condition,
    validate_template,
)
from services.api import schemas
from services.api.auth import AdminUser, CurrentUser
from services.api.db import get_conn
from services.api.domain.config import bump_config_version
from services.api.errors import conflict, forbidden, invalid, not_found
from services.delivery.schedule import Rotation, parse_window, zone

router = APIRouter()
Conn = Annotated[psycopg.Connection, Depends(get_conn)]
Document = Annotated[Any, Body()]
Slug = Annotated[str, Query(pattern=r"^[a-z0-9][a-z0-9_.-]*$", max_length=100)]
LEVELS = ("info", "warning", "error", "critical")


def _check(schema: str, body: Any) -> None:
    if errors := schemas.errors(schema, body):
        raise invalid(errors, f"body does not match {schema}")


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _rows(conn: psycopg.Connection, sql: str, params: Any = ()) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(sql, params).fetchall()


# --- mappings and unmapped (admin, spec §3) -----------------------------------------------------------


def _mapping_out(r: Mapping[str, Any]) -> dict[str, Any]:
    out = {"id": r["id"], "priority": r["priority"], "sources": r["source_ids"] or [], "fields": r["fields"] or {}}
    out |= {"schemaFrom": r["schema_from"], "splitOn": r["split_on"]}
    if r["detect"] is not None:
        out["detect"] = r["detect"]
    return out


def _mapping_errors(doc: Mapping[str, Any]) -> list[dict[str, str]]:
    errors = validate_condition(doc["detect"], "detect") if doc.get("detect") is not None else []
    for name, spec in (doc.get("fields") or {}).items():
        where = f"fields.{name}"
        if not name.startswith("$"):
            errors.append({"path": where, "message": "mapped fields start with `$` (spec §3)"})
        if isinstance(spec, str) and "{{" in spec:
            errors += validate_template(spec, where)
        elif isinstance(spec, str) or (isinstance(spec, Mapping) and "from" in spec):
            try:
                parse_path(spec if isinstance(spec, str) else spec["from"])
            except (PathError, TypeError) as exc:
                errors.append({"path": where, "message": str(exc)})
    for key in ("schemaFrom", "splitOn"):
        if doc.get(key):
            try:
                parse_path(doc[key])
            except PathError as exc:
                errors.append({"path": key, "message": str(exc)})
    return errors


def _save_mapping(conn: psycopg.Connection, doc: Mapping[str, Any], actor: str, create: bool) -> dict[str, Any]:
    _check("Mapping", doc)
    if errors := _mapping_errors(doc):
        raise invalid(errors, "mapping is invalid")
    params = {
        "id": doc["id"], "priority": doc.get("priority", 100), "source_ids": doc.get("sources") or None,
        "detect": Jsonb(doc.get("detect")) if doc.get("detect") is not None else None,
        "schema_from": doc.get("schemaFrom"), "split_on": doc.get("splitOn"), "fields": Jsonb(doc["fields"]),
        "actor": actor,
    }  # fmt: skip
    if create:
        sql = """INSERT INTO schema_mappings
                     (id, priority, source_ids, detect, schema_from, split_on, fields, updated_by)
                 VALUES (%(id)s, %(priority)s, %(source_ids)s, %(detect)s, %(schema_from)s, %(split_on)s, %(fields)s,
                         %(actor)s) ON CONFLICT (id) DO NOTHING RETURNING *"""
    else:
        sql = """UPDATE schema_mappings SET priority = %(priority)s, source_ids = %(source_ids)s, detect = %(detect)s,
                    schema_from = %(schema_from)s, split_on = %(split_on)s, fields = %(fields)s,
                    updated_by = %(actor)s, updated_at = now() WHERE id = %(id)s RETURNING *"""
    rows = _rows(conn, sql, params)
    if not rows:
        raise conflict(f"mapping {doc['id']} already exists") if create else not_found(f"mapping {doc['id']}")
    bump_config_version(conn)
    return _mapping_out(rows[0])


@router.get("/mappings", tags=["mappings"])
def list_mappings(conn: Conn, _: AdminUser) -> list[dict[str, Any]]:
    return [_mapping_out(r) for r in _rows(conn, "SELECT * FROM schema_mappings ORDER BY priority, id")]


@router.post("/mappings", status_code=201, tags=["mappings"])
def create_mapping(doc: Document, conn: Conn, user: AdminUser) -> dict[str, Any]:
    return _save_mapping(conn, doc, user.id, create=True)


@router.put("/mappings/{mappingId}", tags=["mappings"])
def replace_mapping(mappingId: str, doc: Document, conn: Conn, user: AdminUser) -> dict[str, Any]:
    if isinstance(doc, dict) and doc.get("id") != mappingId:
        raise invalid([{"path": "id", "message": "id in the body must match the URL"}])
    return _save_mapping(conn, doc, user.id, create=False)


@router.delete("/mappings/{mappingId}", status_code=204, tags=["mappings"])
def delete_mapping(mappingId: str, conn: Conn, _: AdminUser) -> Response:
    if conn.execute("DELETE FROM schema_mappings WHERE id = %s", (mappingId,)).rowcount == 0:
        raise not_found(f"mapping {mappingId}")
    bump_config_version(conn)
    return Response(status_code=204)


@router.post("/mappings/preview", tags=["mappings"])
def preview_mapping(body: Document, conn: Conn, _: AdminUser) -> dict[str, Any]:
    """Apply an unsaved mapping to a sample (the live preview in the mapping editor). Writes nothing."""
    if not isinstance(body, dict) or "mapping" not in body or "sample" not in body:
        raise invalid([{"path": "body", "message": "needs `mapping` and `sample`"}])
    mapping, sample = body["mapping"], body["sample"]
    errors = schemas.errors("Mapping", mapping) + schemas.errors("TestEvent", sample)
    errors += _mapping_errors(mapping) if not errors else []
    if errors:
        return {"letters": [], "errors": errors}
    source_id = sample.get("sourceId") or "http-default"
    row = _rows(conn, "SELECT * FROM sources WHERE id = %s", (source_id,))
    source = Source(id=source_id, type=row[0]["type"], parser=row[0]["parser"], parser_config=row[0]["parser_config"],
                    default_severity=row[0]["default_severity"]) if row else Source(id=source_id)  # fmt: skip
    schema_mapping = SchemaMapping.from_doc({**mapping, "name": mapping["id"], "sources": None})
    envelope = Envelope("preview", source_id, datetime.now(UTC), sample.get("contentType") or "application/json",
                        sample["payload"])  # fmt: skip
    mapped = map_event(envelope, source, [schema_mapping])
    if mapped and mapped[0].mapping is None:
        errors.append({"path": "detect", "message": "the sample does not match `detect`"})
    return {"letters": [m.letter for m in mapped], "errors": errors}


@router.get("/unmapped", tags=["mappings"])
def list_unmapped(conn: Conn, _: AdminUser) -> list[dict[str, Any]]:
    rows = _rows(conn, "SELECT * FROM unmapped_samples ORDER BY last_seen DESC LIMIT 200")
    return [
        {"shapeHash": r["shape_hash"], "sourceId": r["source_id"], "keys": shape_keys(r["sample"]),
         "count": r["count"], "lastSeen": _iso(r["last_seen"]), "sample": r["sample"]}
        for r in rows
    ]  # fmt: skip


# --- sources (admin) ----------------------------------------------------------------------------------


def _source_out(r: Mapping[str, Any], token: str | None = None) -> dict[str, Any]:
    out = {"id": r["id"], "type": r["type"], "config": r["config"], "parser": r["parser"],
           "defaultSeverity": r["default_severity"], "enabled": r["enabled"]}  # fmt: skip
    if token:
        out["token"] = token
    return out


@router.get("/sources", tags=["admin"])
def list_sources(conn: Conn, _: AdminUser) -> list[dict[str, Any]]:
    return [_source_out(r) for r in _rows(conn, "SELECT * FROM sources ORDER BY id")]


@router.post("/sources", status_code=201, tags=["admin"])
def create_source(doc: Document, conn: Conn, user: AdminUser) -> dict[str, Any]:
    """An HTTP source gets a new ingest token, returned only in this response (stored as SHA-256, spec §11)."""
    _check("Source", doc)
    token = secrets.token_urlsafe(24) if doc["type"] == "http" else None
    rows = _rows(
        conn,
        """INSERT INTO sources (id, type, config, parser, default_severity, enabled, token_sha256, updated_by)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING RETURNING *""",
        (doc["id"], doc["type"], Jsonb(doc.get("config", {})), doc.get("parser", "json"),
         doc.get("defaultSeverity", "info"), doc.get("enabled", True),
         hashlib.sha256(token.encode()).hexdigest() if token else None, user.id),
    )  # fmt: skip
    if not rows:
        raise conflict(f"source {doc['id']} already exists")
    bump_config_version(conn)
    return _source_out(rows[0], token)


@router.put("/sources/{sourceId}", tags=["admin"])
def replace_source(sourceId: str, doc: Document, conn: Conn, user: AdminUser) -> dict[str, Any]:
    _check("Source", doc)
    rows = _rows(
        conn,
        """UPDATE sources SET type = %s, config = %s, parser = %s, default_severity = %s, enabled = %s,
                              updated_by = %s, updated_at = now() WHERE id = %s RETURNING *""",
        (doc["type"], Jsonb(doc.get("config", {})), doc.get("parser", "json"), doc.get("defaultSeverity", "info"),
         doc.get("enabled", True), user.id, sourceId),
    )  # fmt: skip
    if not rows:
        raise not_found(f"source {sourceId}")
    bump_config_version(conn)
    return _source_out(rows[0])


@router.delete("/sources/{sourceId}", status_code=204, tags=["admin"])
def delete_source(sourceId: str, conn: Conn, _: AdminUser) -> Response:
    if conn.execute("DELETE FROM sources WHERE id = %s", (sourceId,)).rowcount == 0:
        raise not_found(f"source {sourceId}")
    bump_config_version(conn)
    return Response(status_code=204)


# --- users and teams (admin; onboarding in one call, spec §9, §13 test 20) ----------------------------


def _user_out(r: Mapping[str, Any]) -> dict[str, Any]:
    return {"id": r["id"], "name": r["name"], "email": r["email"], "phone": r["phone"], "timezone": r["timezone"],
            "isAdmin": r["is_admin"]}  # fmt: skip


def _insert_user(conn: psycopg.Connection, doc: Mapping[str, Any]) -> dict[str, Any]:
    rows = _rows(
        conn,
        """INSERT INTO users (id, name, email, phone, timezone, is_admin) VALUES (%s, %s, %s, %s, %s, %s)
           ON CONFLICT (id) DO NOTHING RETURNING *""",
        (doc["id"], doc["name"], doc.get("email"), doc.get("phone"), doc.get("timezone", "UTC"),
         doc.get("isAdmin", False)),
    )  # fmt: skip
    if not rows:
        raise conflict(f"user {doc['id']} already exists")
    return _user_out(rows[0])


@router.get("/users", tags=["admin"])
def list_users(conn: Conn, _: CurrentUser) -> list[dict[str, Any]]:
    return [_user_out(r) for r in _rows(conn, "SELECT * FROM users ORDER BY name, id")]


@router.post("/users", status_code=201, tags=["admin"])
def create_user(doc: Document, conn: Conn, _: AdminUser) -> dict[str, Any]:
    _check("User", doc)
    return _insert_user(conn, doc)


@router.put("/users/{userId}", tags=["admin"])
def replace_user(userId: str, doc: Document, conn: Conn, _: AdminUser) -> dict[str, Any]:
    _check("User", doc)
    rows = _rows(
        conn,
        "UPDATE users SET name = %s, email = %s, phone = %s, timezone = %s, is_admin = %s WHERE id = %s RETURNING *",
        (
            doc["name"],
            doc.get("email"),
            doc.get("phone"),
            doc.get("timezone", "UTC"),
            doc.get("isAdmin", False),
            userId,
        ),
    )
    if not rows:
        raise not_found(f"user {userId}")
    return _user_out(rows[0])


@router.delete("/users/{userId}", status_code=204, tags=["admin"])
def delete_user(userId: str, conn: Conn, _: AdminUser) -> Response:
    if conn.execute("DELETE FROM users WHERE id = %s", (userId,)).rowcount == 0:
        raise not_found(f"user {userId}")
    return Response(status_code=204)


def _teams(conn: psycopg.Connection, team_id: str | None = None) -> list[dict[str, Any]]:
    rows = _rows(
        conn,
        """SELECT t.id, t.name, coalesce(json_agg(json_build_object('userId', m.user_id, 'role', m.role)
                  ORDER BY m.user_id) FILTER (WHERE m.user_id IS NOT NULL), '[]') AS members
           FROM teams t LEFT JOIN team_members m ON m.team_id = t.id
           WHERE %(id)s::text IS NULL OR t.id = %(id)s GROUP BY t.id, t.name ORDER BY t.id""",
        {"id": team_id},
    )
    return [{"id": r["id"], "name": r["name"], "members": r["members"]} for r in rows]


def _set_members(conn: psycopg.Connection, team_id: str, doc: Mapping[str, Any]) -> None:
    for user in doc.get("newUsers") or []:
        _insert_user(conn, user)
    conn.execute("DELETE FROM team_members WHERE team_id = %s", (team_id,))
    for member in doc.get("members") or []:
        try:
            with conn.transaction():
                conn.execute(
                    "INSERT INTO team_members (team_id, user_id, role) VALUES (%s, %s, %s)",
                    (team_id, member["userId"], member["role"]),
                )
        except psycopg.errors.ForeignKeyViolation as exc:
            raise invalid([{"path": "members", "message": f"unknown user {member['userId']}"}]) from exc


@router.get("/teams", tags=["admin"])
def list_teams(conn: Conn, _: CurrentUser) -> list[dict[str, Any]]:
    return _teams(conn)


@router.post("/teams", status_code=201, tags=["admin"])
def create_team(doc: Document, conn: Conn, _: AdminUser) -> dict[str, Any]:
    """Team + members + new users in one transaction: onboarding a team is one API call (spec §9)."""
    _check("TeamInput", doc)
    if conn.execute("INSERT INTO teams (id, name) VALUES (%s, %s) ON CONFLICT (id) DO NOTHING",
                    (doc["id"], doc["name"])).rowcount == 0:  # fmt: skip
        raise conflict(f"team {doc['id']} already exists")
    _set_members(conn, doc["id"], doc)
    return _teams(conn, doc["id"])[0]


@router.put("/teams/{teamId}", tags=["admin"])
def replace_team(teamId: str, doc: Document, conn: Conn, _: AdminUser) -> dict[str, Any]:
    _check("TeamInput", doc)
    if conn.execute("UPDATE teams SET name = %s WHERE id = %s", (doc["name"], teamId)).rowcount == 0:
        raise not_found(f"team {teamId}")
    _set_members(conn, teamId, doc)
    return _teams(conn, teamId)[0]


@router.delete("/teams/{teamId}", status_code=204, tags=["admin"])
def delete_team(teamId: str, conn: Conn, _: AdminUser) -> Response:
    if conn.execute("DELETE FROM teams WHERE id = %s", (teamId,)).rowcount == 0:
        raise not_found(f"team {teamId}")
    return Response(status_code=204)


# --- my preferences (spec §6, §9 P2) ------------------------------------------------------------------
# Stored as channel_preferences.by_severity (B's format, services/delivery/README.md): a channel list per severity,
# or with working hours a schedule {"schedule": [{between, days, use}], "otherwise": [...]}.


def _prefs_to_store(doc: Mapping[str, Any]) -> dict[str, Any]:
    hours, outside = doc.get("workingHours"), doc.get("outsideWorkingHours")
    stored: dict[str, Any] = {}
    for level, channels in (doc.get("bySeverity") or {}).items():
        if hours and outside is not None:
            window = {"between": hours["between"], "use": channels}
            if hours.get("days"):
                window["days"] = hours["days"]
            stored[level] = {"schedule": [window], "otherwise": outside}
        else:
            stored[level] = channels
    return stored


def _prefs_from_store(timezone: str, by_severity: Mapping[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {"timezone": timezone, "workingHours": None, "bySeverity": {}}
    for level, spec in (by_severity or {}).items():
        if isinstance(spec, Mapping) and spec.get("schedule"):
            window = spec["schedule"][0]
            hours = {"between": window["between"]}
            if window.get("days"):
                hours["days"] = window["days"]
            out["workingHours"] = hours
            out["outsideWorkingHours"] = spec.get("otherwise", [])
            out["bySeverity"][level] = window["use"]
        elif isinstance(spec, list):
            out["bySeverity"][level] = spec
    return out


@router.get("/preferences/me", tags=["preferences"])
def get_my_preferences(conn: Conn, user: CurrentUser) -> dict[str, Any]:
    row = _rows(
        conn,
        """SELECT u.timezone, p.by_severity FROM users u
           LEFT JOIN channel_preferences p ON p.owner_type = 'user' AND p.owner_id = u.id WHERE u.id = %s""",
        (user.id,),
    )[0]
    return _prefs_from_store(row["timezone"], row["by_severity"])


@router.put("/preferences/me", tags=["preferences"])
def replace_my_preferences(doc: Document, conn: Conn, user: CurrentUser) -> dict[str, Any]:
    _check("Preferences", doc)
    if doc.get("workingHours") and doc.get("outsideWorkingHours") is None:
        raise invalid([{"path": "outsideWorkingHours", "message": "working hours need channels for outside them"}])
    if doc.get("timezone"):
        try:
            zone(doc["timezone"]).key  # noqa: B018 - validates the name
        except Exception as exc:  # noqa: BLE001
            raise invalid([{"path": "timezone", "message": "unknown time zone"}]) from exc
        conn.execute("UPDATE users SET timezone = %s WHERE id = %s", (doc["timezone"], user.id))
    conn.execute(
        """INSERT INTO channel_preferences (owner_type, owner_id, by_severity) VALUES ('user', %s, %s)
           ON CONFLICT (owner_type, owner_id) DO UPDATE SET by_severity = EXCLUDED.by_severity, updated_at = now()""",
        (user.id, Jsonb(_prefs_to_store(doc))),
    )
    return get_my_preferences(conn, user)


# --- on-call (spec §6 "Dyżury", §9 P3) ----------------------------------------------------------------


def _oncall_out(r: Mapping[str, Any], now: datetime) -> dict[str, Any]:
    rotation = Rotation(r["team_id"], tuple(r["rotation"]), r["shift_length"], r["handoff_time"], r["timezone"],
                        r["starts_on"])  # fmt: skip
    tz = zone(rotation.timezone)
    anchor = datetime.combine(rotation.starts_on, rotation.handoff_time)
    shifts = (now.astimezone(tz).replace(tzinfo=None) - anchor) // rotation.shift_length
    until = (anchor + (shifts + 1) * rotation.shift_length).replace(tzinfo=tz)
    hours = int(r["shift_length"].total_seconds() // 3600)
    return {
        "teamId": r["team_id"], "rotation": list(r["rotation"]),
        "shiftLength": f"{hours // 24}d" if hours % 24 == 0 else f"{hours}h",
        "handoffTime": r["handoff_time"].strftime("%H:%M"), "timezone": r["timezone"],
        "startsOn": r["starts_on"].isoformat(), "currentUserId": rotation.on_call(now),
        "currentUntil": until.isoformat(),
    }  # fmt: skip


@router.get("/oncall", tags=["oncall"])
def list_oncall(conn: Conn, _: CurrentUser, team: Slug | None = None) -> list[dict[str, Any]]:
    rows = _rows(conn, "SELECT * FROM oncall_schedules WHERE %(t)s::text IS NULL OR team_id = %(t)s ORDER BY team_id",
                 {"t": team})  # fmt: skip
    now = datetime.now(UTC)
    return [_oncall_out(r, now) for r in rows]


@router.put("/oncall/{teamId}", tags=["oncall"])
def replace_oncall(teamId: str, doc: Document, conn: Conn, user: CurrentUser) -> dict[str, Any]:
    if not user.is_manager(teamId):
        raise forbidden(f"manager of team {teamId} required")
    _check("OncallSchedule", {**doc, "teamId": teamId} if isinstance(doc, dict) else doc)
    members = {r["user_id"] for r in _rows(conn, "SELECT user_id FROM team_members WHERE team_id = %s", (teamId,))}
    outsiders = [u for u in doc["rotation"] if u not in members]
    if outsiders:
        raise invalid([{"path": "rotation", "message": f"not members of {teamId}: {', '.join(outsiders)}"}])
    starts = date.fromisoformat(doc["startsOn"]) if doc.get("startsOn") else datetime.now(UTC).date()
    hh, mm = (int(x) for x in doc["handoffTime"].split(":"))
    conn.execute(
        """INSERT INTO oncall_schedules (team_id, rotation, shift_length, handoff_time, timezone, starts_on)
           VALUES (%s, %s, %s, %s, %s, %s)
           ON CONFLICT (team_id) DO UPDATE SET rotation = EXCLUDED.rotation, shift_length = EXCLUDED.shift_length,
               handoff_time = EXCLUDED.handoff_time, timezone = EXCLUDED.timezone, starts_on = EXCLUDED.starts_on,
               updated_at = now()""",
        (teamId, doc["rotation"], parse_window(doc["shiftLength"]), time(hh, mm), doc["timezone"], starts),
    )
    saved = _rows(conn, "SELECT * FROM oncall_schedules WHERE team_id = %s", (teamId,))[0]
    return _oncall_out(saved, datetime.now(UTC))


# --- suggestions ("propose fixes", spec §9) -----------------------------------------------------------


def _suggestion_out(r: Mapping[str, Any]) -> dict[str, Any]:
    return {"id": r["id"], "alertId": r["alert_id"], "target": r["target_type"], "targetId": r["target_id"],
            "text": r["text"], "author": r["author"], "status": r["status"], "decidedBy": r["decided_by"],
            "decidedAt": _iso(r["decided_at"]), "createdAt": _iso(r["created_at"])}  # fmt: skip


def _target_team(conn: psycopg.Connection, target: str, target_id: str) -> str | None:
    if target == "rule":
        row = _rows(conn, "SELECT owner->>'team' AS team FROM rules WHERE id = %s", (target_id,))
    else:
        row = _rows(conn, "SELECT owner_team AS team FROM runbooks WHERE id = %s", (target_id,))
    return row[0]["team"] if row else None


@router.post("/alerts/{alertId}/suggestions", status_code=201, tags=["suggestions"])
def create_suggestion(alertId: int, doc: Document, conn: Conn, user: CurrentUser) -> dict[str, Any]:
    _check("SuggestionInput", doc)
    alert = _rows(conn, "SELECT rule_id FROM alerts WHERE id = %s", (alertId,))
    if not alert:
        raise not_found(f"alert {alertId}")
    target_id = doc.get("targetId") or (alert[0]["rule_id"] if doc["target"] == "rule" else None)
    if not target_id:
        raise invalid([{"path": "targetId", "message": "which runbook? give `targetId`"}])
    rows = _rows(
        conn,
        """INSERT INTO suggestions (alert_id, target_type, target_id, text, author) VALUES (%s, %s, %s, %s, %s)
           RETURNING *""",
        (alertId, doc["target"], target_id, doc["text"], user.id),
    )
    return _suggestion_out(rows[0])


@router.get("/suggestions", tags=["suggestions"])
def list_suggestions(conn: Conn, _: CurrentUser, team: Slug | None = None) -> list[dict[str, Any]]:
    rows = _rows(
        conn,
        """SELECT s.* FROM suggestions s
           LEFT JOIN rules r ON s.target_type = 'rule' AND r.id = s.target_id
           LEFT JOIN runbooks b ON s.target_type = 'runbook' AND b.id = s.target_id
           WHERE %(team)s::text IS NULL OR coalesce(r.owner->>'team', b.owner_team) = %(team)s
           ORDER BY s.id DESC LIMIT 200""",
        {"team": team},
    )
    return [_suggestion_out(r) for r in rows]


@router.post("/suggestions/{suggestionId}/{decision}", tags=["suggestions"])
def decide_suggestion(
    suggestionId: int, decision: Literal["accept", "reject"], conn: Conn, user: CurrentUser
) -> dict[str, Any]:
    rows = _rows(conn, "SELECT * FROM suggestions WHERE id = %s FOR UPDATE", (suggestionId,))
    if not rows:
        raise not_found(f"suggestion {suggestionId}")
    s = rows[0]
    team = _target_team(conn, s["target_type"], s["target_id"])
    if not (user.is_admin or (team and user.is_manager(team))):
        raise forbidden("manager of the team owning the rule or runbook required")
    if s["status"] != "open":
        raise conflict("this suggestion has already been decided")
    status = "accepted" if decision == "accept" else "rejected"
    rows = _rows(
        conn,
        "UPDATE suggestions SET status = %s, decided_by = %s, decided_at = now() WHERE id = %s RETURNING *",
        (status, user.id, suggestionId),
    )
    return _suggestion_out(rows[0])
