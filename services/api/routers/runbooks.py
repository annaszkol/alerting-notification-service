"""Runbooks, runbook runs and approvals (contracts/openapi.yaml tags `runbooks`, `runs`, `approvals`; spec §7, §9 P2).

Thin routes: logic is B's (`services.executor.runbooks`, `.engine`, `.approvals`). Permissions (spec §9):
- read: any logged-in user
- edit / approve / mode / delete a runbook: manager of its owner team (or admin)
- start a run, start a step, tick a manual step: member of a team the alert belongs to (or admin)
- decide an approval: checked by B against the role required by the step
- email link (`/approvals/by-token/...`): no login; the signed token names the person it was sent to
"""

from __future__ import annotations

from datetime import UTC, datetime
from functools import cache
from typing import Annotated, Any, Literal

import psycopg
from fastapi import APIRouter, Depends, Path, Query, Request, Response
from pydantic import BaseModel, Field

from services.api import schemas
from services.api.auth import CurrentUser, Principal
from services.api.db import get_conn
from services.api.domain.alerts import get_rows, summary
from services.api.errors import ApiError, conflict, forbidden, invalid, not_found
from services.executor import runbooks as rb
from services.executor.actions.builtin import build_registry
from services.executor.approvals import decide_approval, decide_by_token, verify_approval_token
from services.executor.engine import Executor, ExecutorError

router = APIRouter()
Conn = Annotated[psycopg.Connection, Depends(get_conn)]
EPOCH = datetime(1970, 1, 2, tzinfo=UTC)


@cache
def actions() -> dict[str, Any]:
    """Action registry for metadata and approval levels; the executor process runs the actions."""
    return build_registry(conninfo="")


def executor() -> Executor:
    return Executor(actions=actions())


def _map(exc: Exception) -> ApiError:
    if isinstance(exc, rb.NotFound):
        return not_found(str(exc))
    if isinstance(exc, rb.Invalid):
        return invalid(exc.errors)
    return conflict(str(exc))


def _alert(conn: psycopg.Connection, request: Request, alert_id: int) -> dict[str, Any]:
    rows = get_rows(conn, [alert_id])
    if not rows:
        raise not_found(f"alert {alert_id}")
    return {"row": rows[0], "summary": summary(rows[0], request.app.state.settings.public_base_url)}


def _require_alert_member(user: Principal, alert: dict[str, Any]) -> None:
    if not any(user.is_member(team) for team in alert["summary"]["teams"]):
        raise forbidden("only members of the alert's teams can run its runbooks")


def _require_runbook_manager(user: Principal, row: dict[str, Any]) -> None:
    if not user.is_manager(row["owner_team"]):
        raise forbidden(f"manager of team {row['owner_team']} required")


def _now() -> datetime:
    return datetime.now(UTC)


# --- runbooks ------------------------------------------------------------------------------------------


@router.get("/runbooks", tags=["runbooks"])
def list_runbooks(conn: Conn, _: CurrentUser, team: Annotated[str | None, Query(max_length=100)] = None):
    return rb.list_runbooks(conn, team)


@router.get("/runbooks/{runbookId}", tags=["runbooks"])
def get_runbook(runbookId: str, conn: Conn, _: CurrentUser) -> dict[str, Any]:
    try:
        return rb.to_api(rb.get_row(conn, runbookId))
    except rb.NotFound as exc:
        raise _map(exc) from exc


@router.put("/runbooks/{runbookId}", tags=["runbooks"])
def replace_runbook(runbookId: str, body: dict[str, Any], conn: Conn, user: CurrentUser) -> dict[str, Any]:
    if errors := schemas.errors("RunbookInput", body):
        raise invalid(errors)
    try:
        _require_runbook_manager(user, rb.get_row(conn, runbookId))
        return rb.replace(conn, runbookId, body["title"], body["steps"], body.get("match"), user.id)
    except (rb.NotFound, rb.Invalid) as exc:
        raise _map(exc) from exc


class ModeRequest(BaseModel):
    model_config = {"extra": "forbid"}
    mode: Literal["manual", "auto"]


@router.patch("/runbooks/{runbookId}", tags=["runbooks"])
def set_runbook_mode(runbookId: str, body: ModeRequest, conn: Conn, user: CurrentUser) -> dict[str, Any]:
    try:
        _require_runbook_manager(user, rb.get_row(conn, runbookId))
        return rb.set_mode(conn, runbookId, body.mode, user.id)
    except rb.NotFound as exc:
        raise _map(exc) from exc


@router.delete("/runbooks/{runbookId}", status_code=204, tags=["runbooks"])
def delete_runbook(runbookId: str, conn: Conn, user: CurrentUser) -> Response:
    try:
        _require_runbook_manager(user, rb.get_row(conn, runbookId))
        rb.delete(conn, runbookId)
    except rb.NotFound as exc:
        raise _map(exc) from exc
    return Response(status_code=204)


@router.post("/runbooks/{runbookId}/approve", tags=["runbooks"])
def approve_runbook(runbookId: str, conn: Conn, user: CurrentUser) -> dict[str, Any]:
    try:
        _require_runbook_manager(user, rb.get_row(conn, runbookId))
        return rb.approve(conn, runbookId, user.id)
    except rb.NotFound as exc:
        raise _map(exc) from exc


@router.get("/actions", tags=["runbooks"])
def list_actions(_: CurrentUser) -> list[dict[str, Any]]:
    return [
        {
            "name": a.name,
            "description": (type(a).__doc__ or "").strip().splitlines()[0] if type(a).__doc__ else "",
            "paramsSchema": a.params_schema,
            "minApproval": a.min_approval.value,
        }
        for a in sorted(actions().values(), key=lambda a: a.name)
    ]


# --- runs ----------------------------------------------------------------------------------------------


class StartRunRequest(BaseModel):
    model_config = {"extra": "forbid"}
    runbookId: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]*$", max_length=100)


@router.post("/alerts/{alertId}/runbook-runs", status_code=201, tags=["runs"])
def start_run(alertId: int, body: StartRunRequest, request: Request, conn: Conn, user: CurrentUser):
    alert = _alert(conn, request, alertId)
    _require_alert_member(user, alert)
    try:
        row = rb.get_row(conn, body.runbookId)
    except rb.NotFound as exc:
        raise _map(exc) from exc
    if row["approved_by"] is None:
        raise conflict("runbook is not approved by a manager yet; its actions cannot run (spec §7)")
    if rb.active_run(conn, alertId, body.runbookId):
        raise conflict("this runbook is already running for this alert")
    run_id = executor().start_run(conn, alertId, row["canonical"], _now(), started_by=user.id, mode=row["mode"])
    return rb.run_view(conn, run_id)


@router.get("/runbook-runs/{runId}", tags=["runs"])
def get_run(runId: int, conn: Conn, _: CurrentUser) -> dict[str, Any]:
    try:
        return rb.run_view(conn, runId)
    except rb.NotFound as exc:
        raise _map(exc) from exc


def _step_action(request: Request, conn: psycopg.Connection, user: Principal, run_id: int) -> None:
    try:
        alert_id = rb.run_alert(conn, run_id)
    except rb.NotFound as exc:
        raise _map(exc) from exc
    _require_alert_member(user, _alert(conn, request, alert_id))


@router.post("/runbook-runs/{runId}/steps/{stepId}/start", status_code=202, tags=["runs"])
def start_step(runId: int, stepId: str, request: Request, conn: Conn, user: CurrentUser) -> dict[str, Any]:
    _step_action(request, conn, user, runId)
    try:
        executor().run_step(conn, rb.latest_step_run(conn, runId, stepId), user.id, _now())
    except (rb.Conflict, ExecutorError) as exc:
        raise conflict(str(exc)) from exc
    return rb.step_view(rb.run_view(conn, runId), stepId)


class DoneRequest(BaseModel):
    model_config = {"extra": "forbid"}
    note: str | None = Field(default=None, max_length=2000)


@router.post("/runbook-runs/{runId}/steps/{stepId}/done", tags=["runs"])
def mark_step_done(
    runId: int, stepId: str, request: Request, conn: Conn, user: CurrentUser, body: DoneRequest | None = None
) -> dict[str, Any]:
    _step_action(request, conn, user, runId)
    try:
        step_run_id = rb.latest_step_run(conn, runId, stepId)
        executor().mark_done(conn, step_run_id, user.id, _now())
    except (rb.Conflict, ExecutorError) as exc:
        raise conflict(str(exc)) from exc
    rb.set_step_note(conn, step_run_id, body.note if body else None)
    return rb.step_view(rb.run_view(conn, runId), stepId)


# --- approvals -----------------------------------------------------------------------------------------


def _decision_result(conn: psycopg.Connection, approval_id: int, result: str) -> dict[str, Any]:
    match result:
        case "approved" | "rejected":
            return rb.approval_view(rb.get_approval(conn, approval_id))
        case "not_allowed":
            raise forbidden("the required role in the approval's team is needed")
        case "already_decided":
            raise conflict("this approval has already been decided")
        case _:
            raise not_found(f"approval {approval_id}")


@router.get("/approvals", tags=["approvals"])
def list_approvals(
    conn: Conn,
    user: CurrentUser,
    status: Annotated[Literal["pending", "approved", "rejected"] | None, Query()] = None,
    mine: bool = False,
) -> list[dict[str, Any]]:
    return rb.list_approvals(conn, status, user.id if mine else None)


def _decide(conn: psycopg.Connection, approval_id: int, user: Principal, decision: str) -> dict[str, Any]:
    try:
        rb.get_approval(conn, approval_id)
    except rb.NotFound as exc:
        raise _map(exc) from exc
    result = decide_approval(conn, approval_id, user.id, decision, "ui")
    return _decision_result(conn, approval_id, result.result)


@router.post("/approvals/{approvalId}/approve", tags=["approvals"])
def approve_step(approvalId: int, conn: Conn, user: CurrentUser) -> dict[str, Any]:
    return _decide(conn, approvalId, user, "approve")


@router.post("/approvals/{approvalId}/reject", tags=["approvals"])
def reject_step(approvalId: int, conn: Conn, user: CurrentUser) -> dict[str, Any]:
    """Rejection ends the run (spec §7)."""
    return _decide(conn, approvalId, user, "reject")


def _verify(token: str) -> tuple[int, str]:
    verified = verify_approval_token(token, _now())
    if verified is None:
        if verify_approval_token(token, EPOCH) is not None:  # genuine, but expired
            raise ApiError("expired", "this approval link has expired (24 h)")
        raise not_found("approval link")
    return verified


@router.get("/approvals/by-token/{token}", tags=["approvals"])
def get_by_token(token: Annotated[str, Path(min_length=16, max_length=512)], request: Request, conn: Conn):
    approval_id, user_id = _verify(token)
    try:
        row = rb.get_approval(conn, approval_id)
    except rb.NotFound as exc:
        raise _map(exc) from exc
    if row["decision"] is not None:
        raise conflict("this approval has already been decided")
    alert = _alert(conn, request, row["alert_id"])
    step = next(s for s in row["runbook"].get("steps", []) if s["id"] == row["step_id"])
    return {
        "approval": rb.approval_view(row),
        "alert": alert["summary"],
        "step": rb.render_step(step, alert["row"]["first_event"] or {}, actions()),
        "userId": user_id,
    }


class TokenDecision(BaseModel):
    model_config = {"extra": "forbid"}
    decision: Literal["approve", "reject"]


@router.post("/approvals/by-token/{token}", tags=["approvals"])
def decide_by_link(
    token: Annotated[str, Path(min_length=16, max_length=512)], body: TokenDecision, conn: Conn
) -> dict[str, Any]:
    approval_id, _ = _verify(token)
    result = decide_by_token(conn, token, body.decision)
    return _decision_result(conn, approval_id, result.result)
