"""Rules: one JSON document each (spec §4). Every write bumps `config_version` and sends
`NOTIFY config_changed` in the same transaction, so processors reload exactly what was committed
(spec §4 "Zmiany w runtime")."""

from __future__ import annotations

from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from services.api import schemas, stubs
from services.api.domain.config import bump_config_version
from services.api.durations import format_duration, parse_duration
from services.api.errors import conflict, invalid, not_found


def to_api(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "owner": row["owner"],
        "enabled": row["enabled"],
        "when": row["when"],
        "route": row["route"],
        "present": row["present"],
        "dedup": row["dedup"],
        "autoResolveAfter": format_duration(row["auto_resolve_after"]),
        "runbooks": list(row["runbook_ids"]),
        "updatedBy": row["updated_by"],
        "updatedAt": row["updated_at"].isoformat(),
    }


# --- validation ----------------------------------------------------------------------------------------


def validate(conn: psycopg.Connection, doc: Any) -> dict[str, Any]:
    """Contract shape + conditions + templates + references. Raises `invalid` with all errors at once."""
    errors = schemas.errors("RuleInput", doc)
    if errors:
        raise invalid(errors, "rule does not match the contract")
    errors += stubs.validate_condition(doc["when"], "when")
    present = doc.get("present") or {}
    for key in ("title", "body"):
        if key in present:
            errors += stubs.validate_template(present[key], f"present.{key}")
    for i, path in enumerate(present.get("fields") or []):
        try:
            stubs.parse_path(path)
        except stubs.PathError as exc:
            errors.append({"path": f"present.fields.{i}", "message": str(exc)})
    dedup = doc.get("dedup") or {}
    if dedup.get("mode") == "window" and not dedup.get("window"):
        errors.append({"path": "dedup.window", "message": "window mode needs `window`, e.g. 5m"})
    for i, path in enumerate(dedup.get("key") or []):
        try:
            stubs.parse_path(path)
        except stubs.PathError as exc:
            errors.append({"path": f"dedup.key.{i}", "message": str(exc)})
    errors += _missing_references(conn, doc)
    if errors:
        raise invalid(errors, "rule is invalid")
    return doc


def _missing_references(conn: psycopg.Connection, doc: dict[str, Any]) -> list[dict[str, str]]:
    users, teams = set(), set()
    owner = doc["owner"]
    (users if "user" in owner else teams).add((owner.get("user") or owner.get("team"), "owner"))
    for i, entry in enumerate(doc.get("route") or []):
        to = entry["to"]
        if "user" in to:
            users.add((to["user"], f"route.{i}.to.user"))
        else:
            teams.add((to.get("team") or to.get("oncall"), f"route.{i}.to"))
    errors = []
    known_users = _existing(conn, "users", [u for u, _ in users])
    known_teams = _existing(conn, "teams", [t for t, _ in teams])
    errors += [
        {"path": p, "message": f"unknown user `{u}`"} for u, p in sorted(users) if u not in known_users
    ]
    errors += [
        {"path": p, "message": f"unknown team `{t}`"} for t, p in sorted(teams) if t not in known_teams
    ]
    return errors


def _existing(conn: psycopg.Connection, table: str, ids: list[str]) -> set[str]:
    if not ids:
        return set()
    rows = conn.execute(f"SELECT id FROM {table} WHERE id = ANY(%s)", (ids,)).fetchall()  # table is a literal
    return {r["id"] if isinstance(r, dict) else r[0] for r in rows}


# --- reads and writes ----------------------------------------------------------------------------------


def list_rules(
    conn: psycopg.Connection, team: str | None = None, user: str | None = None
) -> list[dict[str, Any]]:
    where, params = [], []
    if team:
        where.append("owner->>'team' = %s")
        params.append(team)
    if user:
        where.append("owner->>'user' = %s")
        params.append(user)
    sql = "SELECT * FROM rules" + (f" WHERE {' AND '.join(where)}" if where else "") + " ORDER BY id"
    return [to_api(r) for r in conn.cursor(row_factory=dict_row).execute(sql, params)]


def get_row(conn: psycopg.Connection, rule_id: str, *, for_update: bool = False) -> dict[str, Any]:
    sql = "SELECT * FROM rules WHERE id = %s" + (" FOR UPDATE" if for_update else "")
    row = conn.cursor(row_factory=dict_row).execute(sql, (rule_id,)).fetchone()
    if row is None:
        raise not_found(f"rule {rule_id}")
    return row


def _values(doc: dict[str, Any], actor: str) -> dict[str, Any]:
    auto = doc.get("autoResolveAfter")
    return {
        "id": doc["id"],
        "owner": Jsonb(doc["owner"]),
        "enabled": doc.get("enabled", True),
        "when": Jsonb(doc["when"]),
        "route": Jsonb(doc.get("route") or []),
        "present": Jsonb(doc.get("present") or {}),
        "dedup": Jsonb(doc.get("dedup") or {}),
        "auto_resolve_after": parse_duration(auto) if auto else None,
        "runbook_ids": doc.get("runbooks") or [],
        "updated_by": actor,
    }


def create(conn: psycopg.Connection, doc: dict[str, Any], actor: str) -> dict[str, Any]:
    row = (
        conn.cursor(row_factory=dict_row)
        .execute(
            """INSERT INTO rules (id, owner, enabled, "when", route, present, dedup, auto_resolve_after,
                                  runbook_ids, updated_by, updated_at)
               VALUES (%(id)s, %(owner)s, %(enabled)s, %(when)s, %(route)s, %(present)s, %(dedup)s,
                       %(auto_resolve_after)s, %(runbook_ids)s, %(updated_by)s, now())
               ON CONFLICT (id) DO NOTHING RETURNING *""",
            _values(doc, actor),
        )
        .fetchone()
    )
    if row is None:
        raise conflict(f"rule {doc['id']} already exists")
    bump_config_version(conn)
    return to_api(row)


def replace(conn: psycopg.Connection, doc: dict[str, Any], actor: str) -> dict[str, Any]:
    row = (
        conn.cursor(row_factory=dict_row)
        .execute(
            """UPDATE rules SET owner = %(owner)s, enabled = %(enabled)s, "when" = %(when)s,
                      route = %(route)s, present = %(present)s, dedup = %(dedup)s,
                      auto_resolve_after = %(auto_resolve_after)s,
                      runbook_ids = %(runbook_ids)s, updated_by = %(updated_by)s, updated_at = now()
               WHERE id = %(id)s RETURNING *""",
            _values(doc, actor),
        )
        .fetchone()
    )
    if row is None:
        raise not_found(f"rule {doc['id']}")
    bump_config_version(conn)
    return to_api(row)


def set_enabled(conn: psycopg.Connection, rule_id: str, enabled: bool, actor: str) -> dict[str, Any]:
    row = (
        conn.cursor(row_factory=dict_row)
        .execute(
            "UPDATE rules SET enabled = %s, updated_by = %s, updated_at = now() WHERE id = %s RETURNING *",
            (enabled, actor, rule_id),
        )
        .fetchone()
    )
    if row is None:
        raise not_found(f"rule {rule_id}")
    bump_config_version(conn)
    return to_api(row)


def delete(conn: psycopg.Connection, rule_id: str) -> None:
    if conn.execute("DELETE FROM rules WHERE id = %s RETURNING id", (rule_id,)).fetchone() is None:
        raise not_found(f"rule {rule_id}")
    bump_config_version(conn)
