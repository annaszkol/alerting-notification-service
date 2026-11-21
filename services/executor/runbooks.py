"""Runbooks, runs and approvals as the API shows them (owner B, spec §7; shapes from contracts/openapi.yaml).

Plain functions on the caller's connection; the API routers (services/api/routers/runbooks.py) only add HTTP and
permission checks. Errors are B's own exceptions so this module does not depend on the API.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg
from jsonschema import Draft202012Validator
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from contracts.actions import Action, Approval, effective_approval
from db.rows import plain_rows
from services.executor.templating import render, render_params

_SCHEMA = json.loads((Path(__file__).resolve().parents[2] / "contracts" / "runbook.schema.json").read_text())
_VALIDATOR = Draft202012Validator(_SCHEMA)


class NotFound(Exception):
    pass


class Conflict(Exception):
    pass


class Invalid(Exception):
    def __init__(self, errors: list[dict[str, str]]) -> None:
        super().__init__("invalid runbook")
        self.errors = errors


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def validate(canonical: Mapping[str, Any]) -> list[dict[str, str]]:
    """Errors against contracts/runbook.schema.json, plus step ids that must be unique and `on_failure` targets."""
    errors = [
        {"path": ".".join(str(p) for p in e.absolute_path) or "body", "message": e.message}
        for e in _VALIDATOR.iter_errors(dict(canonical))
    ]
    ids = [s.get("id") for s in canonical.get("steps", []) if isinstance(s, Mapping)]
    for i, step in enumerate(canonical.get("steps", [])):
        if not isinstance(step, Mapping):
            continue
        if ids.count(step.get("id")) > 1:
            errors.append({"path": f"steps.{i}.id", "message": f"duplicate step id {step.get('id')!r}"})
        if step.get("on_failure") and step["on_failure"] not in ids:
            errors.append({"path": f"steps.{i}.on_failure", "message": f"no step {step['on_failure']!r}"})
    return sorted(errors, key=lambda e: e["path"])


# --- runbooks ------------------------------------------------------------------------------------------


def to_api(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "owner": {"team": row["owner_team"]},
        "title": row["title"],
        "format": row["format"],
        "original": row["original"],
        "canonical": row["canonical"],
        "mode": row["mode"],
        "approvedBy": row["approved_by"],
        "approvedAt": _iso(row["approved_at"]),
        "updatedBy": row["updated_by"],
        "updatedAt": _iso(row["updated_at"]),
    }


def get_row(conn: psycopg.Connection, runbook_id: str, *, for_update: bool = False) -> dict[str, Any]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"SELECT * FROM runbooks WHERE id = %s{' FOR UPDATE' if for_update else ''}", (runbook_id,))
        row = cur.fetchone()
    if row is None:
        raise NotFound(f"runbook {runbook_id}")
    return row


def list_runbooks(conn: psycopg.Connection, team: str | None = None) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT * FROM runbooks WHERE %(team)s::text IS NULL OR owner_team = %(team)s ORDER BY owner_team, id",
            {"team": team},
        )
        return [to_api(r) for r in cur.fetchall()]


def _save(conn: psycopg.Connection, row: Mapping[str, Any], sets: str, params: Mapping[str, Any]) -> dict[str, Any]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"UPDATE runbooks SET {sets} WHERE id = %(id)s RETURNING *", {"id": row["id"], **params})
        return to_api(cur.fetchone())


def replace(
    conn: psycopg.Connection, runbook_id: str, title: str, steps: list[Any], match: Any, actor: str
) -> dict[str, Any]:
    """PUT: new steps / match. Clears the approval: a changed runbook must be approved again (spec §7)."""
    row = get_row(conn, runbook_id, for_update=True)
    canonical = {**row["canonical"], "id": runbook_id, "title": title, "steps": steps}
    canonical.pop("match", None)
    if match is not None:
        canonical["match"] = match
    if errors := validate(canonical):
        raise Invalid(errors)
    return _save(
        conn,
        row,
        "title = %(title)s, canonical = %(canonical)s, approved_by = NULL, approved_at = NULL,"
        " updated_by = %(actor)s, updated_at = now()",
        {"title": title, "canonical": Jsonb(canonical), "actor": actor},
    )


def set_mode(conn: psycopg.Connection, runbook_id: str, mode: str, actor: str) -> dict[str, Any]:
    row = get_row(conn, runbook_id, for_update=True)
    return _save(
        conn,
        row,
        "mode = %(mode)s, canonical = %(canonical)s, updated_by = %(actor)s, updated_at = now()",
        {"mode": mode, "canonical": Jsonb({**row["canonical"], "mode": mode}), "actor": actor},
    )


def approve(conn: psycopg.Connection, runbook_id: str, actor: str) -> dict[str, Any]:
    row = get_row(conn, runbook_id, for_update=True)
    return _save(conn, row, "approved_by = %(actor)s, approved_at = now()", {"actor": actor})


def delete(conn: psycopg.Connection, runbook_id: str) -> None:
    """Runs already started keep their own snapshot of the runbook."""
    if conn.execute("DELETE FROM runbooks WHERE id = %s", (runbook_id,)).rowcount == 0:
        raise NotFound(f"runbook {runbook_id}")


@plain_rows
def create(
    conn: psycopg.Connection,
    canonical: Mapping[str, Any],
    *,
    owner_team: str,
    title: str,
    fmt: str,
    original: str,
    actor: str,
) -> dict[str, Any]:
    """Save an imported runbook, not approved: its actions run only after a manager approves it (spec §7)."""
    if errors := validate(canonical):
        raise Invalid(errors)
    with conn.cursor(row_factory=dict_row) as cur:
        row = cur.execute(
            """INSERT INTO runbooks (id, owner_team, title, format, original, canonical, mode, updated_by)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING RETURNING *""",
            (canonical["id"], owner_team, title, fmt, original, Jsonb(dict(canonical)), canonical.get("mode", "manual"),
             actor),
        ).fetchone()  # fmt: skip
    if row is None:
        raise Conflict(f"runbook {canonical['id']} already exists")
    return to_api(row)


@plain_rows
def upsert(
    conn: psycopg.Connection,
    canonical: Mapping[str, Any],
    *,
    owner_team: str,
    title: str,
    fmt: str,
    original: str,
    approved_by: str | None,
    reset: bool,
) -> int:
    """Seed loader: insert (or with reset: overwrite) one runbook. Returns rows written."""
    if errors := validate(canonical):
        raise Invalid(errors)
    conflict = (
        "DO UPDATE SET owner_team = EXCLUDED.owner_team, title = EXCLUDED.title, format = EXCLUDED.format,"
        " original = EXCLUDED.original, canonical = EXCLUDED.canonical, mode = EXCLUDED.mode,"
        " approved_by = EXCLUDED.approved_by, approved_at = EXCLUDED.approved_at, updated_at = now()"
        if reset
        else "DO NOTHING"
    )
    return conn.execute(
        f"""INSERT INTO runbooks (id, owner_team, title, format, original, canonical, mode, approved_by, approved_at,
                                  updated_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CASE WHEN %s::text IS NULL THEN NULL ELSE now() END, 'seed')
            ON CONFLICT (id) {conflict}""",
        (
            canonical["id"], owner_team, title, fmt, original, Jsonb(dict(canonical)), canonical.get("mode", "manual"),
            approved_by, approved_by,
        ),
    ).rowcount  # fmt: skip


# --- rendering -----------------------------------------------------------------------------------------


def step_title(step: Mapping[str, Any]) -> str:
    if step.get("title"):
        return step["title"]
    if step["kind"] == "text":
        return (step.get("text") or "").strip().splitlines()[0][:120] if step.get("text") else step["id"]
    if step["kind"] == "wait":
        return f"Wait {step.get('duration', '')}"
    return f"{step.get('action', step['id'])}"


def render_step(step: Mapping[str, Any], letter: Mapping[str, Any], actions: Mapping[str, Action]) -> dict[str, Any]:
    """RenderedStep (contracts/openapi.yaml): values from the alert's first event filled in."""
    action = actions.get(step.get("action", "")) if step["kind"] == "action" else None
    minimum = action.min_approval if action else (Approval.REQUIRED if step["kind"] == "action" else Approval.NONE)
    return {
        "id": step["id"],
        "kind": step["kind"],
        "title": render(step_title(step), letter),
        "text": render(step["text"], letter) if step.get("text") else None,
        "action": step.get("action"),
        "params": render_params(step.get("params", {}), letter),
        "duration": step.get("duration"),
        "effectiveApproval": effective_approval(Approval(step.get("approval", "none")), minimum).value,
        "approvalRole": step.get("approval_role", "manager"),
        "needsReview": bool(step.get("needs_review", False)),
    }


@plain_rows
def runbooks_for_alert(
    conn: psycopg.Connection,
    alert_id: int,
    rule_id: str | None,
    letter: Mapping[str, Any],
    actions: Mapping[str, Action],
) -> list[dict[str, Any]]:
    """AlertRunbook list: runbooks attached to the alert's rule (`runbook_ids`), plus any already run on it.
    Runbooks with their own `match` join when the condition evaluator (libs/conditions, owner A) lands."""
    ids: list[str] = []
    if rule_id and conn.execute("SELECT to_regclass('rules') IS NOT NULL").fetchone()[0]:
        row = conn.execute("SELECT runbook_ids FROM rules WHERE id = %s", (rule_id,)).fetchone()
        ids = list(row[0] or []) if row else []
    ran = [r[0] for r in conn.execute("SELECT DISTINCT runbook_id FROM runbook_runs WHERE alert_id = %s", (alert_id,))]
    wanted = ids + [r for r in ran if r not in ids]
    if not wanted:
        return []
    with conn.cursor(row_factory=dict_row) as cur:
        rows = {r["id"]: r for r in cur.execute("SELECT * FROM runbooks WHERE id = ANY(%s)", (wanted,)).fetchall()}
        latest = {
            r["runbook_id"]: r["id"]
            for r in cur.execute(
                "SELECT DISTINCT ON (runbook_id) runbook_id, id FROM runbook_runs WHERE alert_id = %s"
                " ORDER BY runbook_id, id DESC",
                (alert_id,),
            ).fetchall()
        }
    out = []
    for runbook_id in wanted:
        row = rows.get(runbook_id)
        if row is None:
            continue
        canonical = row["canonical"]
        out.append(
            {
                "id": row["id"],
                "title": row["title"],
                "format": row["format"],
                "url": canonical.get("link"),
                "approved": row["approved_by"] is not None,
                "mode": row["mode"],
                "steps": [render_step(s, letter, actions) for s in canonical.get("steps", [])],
                "runId": latest.get(runbook_id),
            }
        )
    return out


# --- runs ----------------------------------------------------------------------------------------------


@plain_rows
def run_view(conn: psycopg.Connection, run_id: int) -> dict[str, Any]:
    """RunbookRun: every step of the snapshot, with its latest step run (id null when not reached yet)."""
    with conn.cursor(row_factory=dict_row) as cur:
        run = cur.execute("SELECT * FROM runbook_runs WHERE id = %s", (run_id,)).fetchone()
        if run is None:
            raise NotFound(f"run {run_id}")
        step_runs = cur.execute(
            """SELECT DISTINCT ON (s.step_id) s.*, a.id AS approval_id
               FROM step_runs s
               LEFT JOIN LATERAL (SELECT id FROM approvals WHERE step_run_id = s.id ORDER BY id DESC LIMIT 1) a ON true
               WHERE s.run_id = %s ORDER BY s.step_id, s.seq DESC""",
            (run_id,),
        ).fetchall()
    by_step = {s["step_id"]: s for s in step_runs}
    steps = []
    for step in run["runbook"].get("steps", []):
        s = by_step.get(step["id"])
        if s is None:
            steps.append({"id": None, "stepId": step["id"], "status": "pending", "attempt": 0})
            continue
        finished = s["status"] in ("succeeded", "failed")
        steps.append(
            {
                "id": s["id"],
                "stepId": s["step_id"],
                "status": s["status"],
                "params": s["params"] or {},
                "result": {"ok": s["status"] == "succeeded", "output": s["result"] or {}, "error": s["error"]}
                if finished or s["error"]
                else None,
                "attempt": s["attempt"],
                "nextRunAt": _iso(s["next_run_at"]),
                "startedBy": s["started_by"],
                "executedBy": s["executed_by"],
                "approvedBy": s["approved_by"],
                "approvalId": s["approval_id"],
                "startedAt": _iso(s["started_at"]),
                "finishedAt": _iso(s["finished_at"]),
                "note": s["note"],
            }
        )
    return {
        "id": run["id"],
        "alertId": run["alert_id"],
        "runbookId": run["runbook_id"],
        "status": run["status"],
        "startedBy": run["started_by"] or "auto",
        "startedAt": _iso(run["created_at"]),
        "finishedAt": _iso(run["finished_at"]),
        "steps": steps,
    }


def step_view(run: Mapping[str, Any], step_id: str) -> dict[str, Any]:
    return next(s for s in run["steps"] if s["stepId"] == step_id)


@plain_rows
def latest_step_run(conn: psycopg.Connection, run_id: int, step_id: str) -> int:
    row = conn.execute(
        "SELECT id FROM step_runs WHERE run_id = %s AND step_id = %s ORDER BY seq DESC LIMIT 1", (run_id, step_id)
    ).fetchone()
    if row is None:
        raise Conflict(f"step {step_id} has not been reached yet")
    return row[0]


@plain_rows
def run_alert(conn: psycopg.Connection, run_id: int) -> int:
    row = conn.execute("SELECT alert_id FROM runbook_runs WHERE id = %s", (run_id,)).fetchone()
    if row is None:
        raise NotFound(f"run {run_id}")
    return row[0]


@plain_rows
def active_run(conn: psycopg.Connection, alert_id: int, runbook_id: str) -> int | None:
    row = conn.execute(
        "SELECT id FROM runbook_runs WHERE alert_id = %s AND runbook_id = %s"
        " AND status IN ('running', 'waiting_approval') ORDER BY id DESC LIMIT 1",
        (alert_id, runbook_id),
    ).fetchone()
    return row[0] if row else None


@plain_rows
def set_step_note(conn: psycopg.Connection, step_run_id: int, note: str | None) -> None:
    if note:
        conn.execute("UPDATE step_runs SET note = %s WHERE id = %s", (note, step_run_id))


# --- approvals -----------------------------------------------------------------------------------------

_APPROVALS_SQL = """
SELECT a.*, s.run_id, s.step_id, r.alert_id, r.runbook
FROM approvals a JOIN step_runs s ON s.id = a.step_run_id JOIN runbook_runs r ON r.id = s.run_id
"""


def approval_view(row: Mapping[str, Any]) -> dict[str, Any]:
    step = next((s for s in row["runbook"].get("steps", []) if s["id"] == row["step_id"]), {"id": row["step_id"]})
    return {
        "id": row["id"],
        "alertId": row["alert_id"],
        "runId": row["run_id"],
        "stepId": row["step_id"],
        "stepTitle": step_title({"kind": "action", **step}),
        "team": row["team_id"],
        "requiredRole": row["required_role"],
        "status": row["decision"] or "pending",
        "decidedBy": row["decided_by"],
        "decidedAt": _iso(row["decided_at"]),
        "via": row["via"],
        "createdAt": _iso(row["created_at"]),
    }


@plain_rows
def get_approval(conn: psycopg.Connection, approval_id: int) -> dict[str, Any]:
    with conn.cursor(row_factory=dict_row) as cur:
        row = cur.execute(_APPROVALS_SQL + " WHERE a.id = %s", (approval_id,)).fetchone()
    if row is None:
        raise NotFound(f"approval {approval_id}")
    return row


@plain_rows
def list_approvals(
    conn: psycopg.Connection, status: str | None = None, mine_for: str | None = None
) -> list[dict[str, Any]]:
    """`status`: pending | approved | rejected. `mine_for`: only approvals this user may decide."""
    where, params = [], {}
    if status == "pending":
        where.append("a.decision IS NULL")
    elif status in ("approved", "rejected"):
        where.append("a.decision = %(status)s")
        params["status"] = status
    if mine_for:
        where.append(
            "EXISTS (SELECT 1 FROM team_members m WHERE m.team_id = a.team_id AND m.user_id = %(user)s"
            " AND (a.required_role = 'member' OR m.role = 'manager'))"
        )
        params["user"] = mine_for
    sql = _APPROVALS_SQL + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY a.id DESC LIMIT 200"
    with conn.cursor(row_factory=dict_row) as cur:
        return [approval_view(r) for r in cur.execute(sql, params).fetchall()]
