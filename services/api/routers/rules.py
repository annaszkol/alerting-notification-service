"""Rule CRUD (spec §4, §9 P1). Team rules: manager of the team; personal rules: the owner; admin: everything.
A change of owner needs the right to edit both the old and the new owner."""

from __future__ import annotations

from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Body, Depends, Query, Response

from services.api.auth import CurrentUser, Principal
from services.api.db import get_conn
from services.api.domain import rules
from services.api.errors import forbidden, invalid

router = APIRouter(tags=["rules"])
Conn = Annotated[psycopg.Connection, Depends(get_conn)]
Slug = Annotated[str, Query(pattern=r"^[a-z0-9][a-z0-9_.-]*$", max_length=100)]
Document = Annotated[Any, Body()]


def _check_owner(user: Principal, owner: dict[str, Any]) -> None:
    if not user.can_edit_owned(owner):
        who = f"manager of team {owner['team']}" if "team" in owner else f"user {owner.get('user')}"
        raise forbidden(f"only the {who} or an admin can change this rule")


@router.get("/rules")
def list_rules(conn: Conn, _: CurrentUser, team: Slug | None = None, user: Slug | None = None) -> list[dict]:
    return rules.list_rules(conn, team=team, user=user)


@router.post("/rules", status_code=201)
def create_rule(doc: Document, conn: Conn, user: CurrentUser) -> dict[str, Any]:
    doc = rules.validate(conn, doc)
    _check_owner(user, doc["owner"])
    return rules.create(conn, doc, user.id)


@router.get("/rules/{ruleId}")
def get_rule(ruleId: str, conn: Conn, _: CurrentUser) -> dict[str, Any]:
    return rules.to_api(rules.get_row(conn, ruleId))


@router.put("/rules/{ruleId}")
def replace_rule(ruleId: str, doc: Document, conn: Conn, user: CurrentUser) -> dict[str, Any]:
    existing = rules.get_row(conn, ruleId, for_update=True)
    _check_owner(user, existing["owner"])
    doc = rules.validate(conn, doc)
    if doc["id"] != ruleId:
        raise invalid([{"path": "id", "message": "id cannot be changed; create a new rule instead"}])
    _check_owner(user, doc["owner"])
    return rules.replace(conn, doc, user.id)


@router.patch("/rules/{ruleId}")
def set_enabled(ruleId: str, doc: Document, conn: Conn, user: CurrentUser) -> dict[str, Any]:
    if not isinstance(doc, dict) or set(doc) != {"enabled"} or not isinstance(doc["enabled"], bool):
        raise invalid([{"path": "enabled", "message": 'body must be {"enabled": true|false}'}])
    _check_owner(user, rules.get_row(conn, ruleId, for_update=True)["owner"])
    return rules.set_enabled(conn, ruleId, doc["enabled"], user.id)


@router.delete("/rules/{ruleId}", status_code=204)
def delete_rule(ruleId: str, conn: Conn, user: CurrentUser) -> Response:
    _check_owner(user, rules.get_row(conn, ruleId, for_update=True)["owner"])
    rules.delete(conn, ruleId)
    return Response(status_code=204)
