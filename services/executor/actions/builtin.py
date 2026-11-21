"""Built-in runbook actions (spec §7 table, contract: contracts/actions.py).

HTTP actions whose URL comes from the runbook (`http_get`, `webhook`) only call hosts on the allowlist, checked
after template substitution (SSRF protection, spec §11). The other HTTP actions call fixed service URLs from the
configuration. Internal actions (`escalate`, `notify`) write notifications in their own short transaction and use a
key derived from the step run, so repeating them after a crash does not send twice.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, ClassVar
from urllib.parse import quote, urlsplit

import httpx
import psycopg

from contracts.actions import ActionContext, ActionResult, Approval
from contracts.delivery import AlertForPlanning, RuleForPlanning, Severity
from contracts.notify import notify_alerts_changed
from services.delivery.planning import NotificationPlanner

HTTP_TIMEOUT = 10.0


def host_allowed(url: str, allowlist: frozenset[str]) -> bool:
    parts = urlsplit(url)
    return parts.scheme in ("http", "https") and (parts.hostname or "") in allowlist


def _summary(response: httpx.Response) -> dict[str, Any]:
    try:
        body: Any = response.json()
    except ValueError:
        body = response.text[:500]
    return {"status": response.status_code, "body": body}


class HttpAction:
    """Base for actions that make one HTTP call and succeed on 2xx."""

    name: ClassVar[str]
    params_schema: ClassVar[dict[str, Any]]
    min_approval: ClassVar[Approval]

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def call(self, method: str, url: str, **kwargs: Any) -> ActionResult:
        try:
            async with httpx.AsyncClient(transport=self.transport, timeout=HTTP_TIMEOUT) as client:
                response = await client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            return ActionResult(ok=False, error=f"{type(exc).__name__}: {exc}")
        output = _summary(response)
        if response.is_success:
            return ActionResult(ok=True, output=output)
        return ActionResult(ok=False, output=output, error=f"HTTP {response.status_code}")


class HttpGet(HttpAction):
    name = "http_get"
    min_approval = Approval.NONE
    params_schema = {
        "type": "object",
        "required": ["url"],
        "properties": {"url": {"type": "string", "title": "URL (allowlisted host)"}},
    }

    async def execute(self, params: dict[str, Any], ctx: ActionContext) -> ActionResult:
        url = params["url"]
        if not host_allowed(url, ctx.http_allowlist):
            return ActionResult(ok=False, error=f"host of {url!r} is not on the allowlist")
        return await self.call("GET", url)


class Webhook(HttpAction):
    name = "webhook"
    min_approval = Approval.REQUIRED
    params_schema = {
        "type": "object",
        "required": ["url"],
        "properties": {
            "url": {"type": "string", "title": "URL (allowlisted host)"},
            "body": {"type": "object", "title": "JSON body"},
        },
    }

    async def execute(self, params: dict[str, Any], ctx: ActionContext) -> ActionResult:
        url = params["url"]
        if not host_allowed(url, ctx.http_allowlist):
            return ActionResult(ok=False, error=f"host of {url!r} is not on the allowlist")
        headers = {"Idempotency-Key": f"step-run-{ctx.step_run_id}-{ctx.attempt}"}
        return await self.call("POST", url, json=params.get("body", {}), headers=headers)


class ServiceAction(HttpAction):
    """POST to a fixed service from the configuration."""

    def __init__(self, base_url: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        super().__init__(transport)
        self.base_url = base_url.rstrip("/")


class RetryWorkflow(ServiceAction):
    name = "retry_workflow"
    min_approval = Approval.NONE
    params_schema = {"type": "object", "required": ["workflowId"], "properties": {"workflowId": {"type": "string"}}}

    async def execute(self, params: dict[str, Any], ctx: ActionContext) -> ActionResult:
        workflow = quote(str(params["workflowId"]), safe="")
        return await self.call("POST", f"{self.base_url}/workflows/{workflow}/retry", json={"attempt": ctx.attempt})


class RestartService(ServiceAction):
    name = "restart_service"
    min_approval = Approval.REQUIRED
    params_schema = {"type": "object", "required": ["service"], "properties": {"service": {"type": "string"}}}

    async def execute(self, params: dict[str, Any], ctx: ActionContext) -> ActionResult:
        service = quote(str(params["service"]), safe="")
        return await self.call("POST", f"{self.base_url}/services/{service}/restart")


class OpenTicket(ServiceAction):
    name = "open_ticket"
    min_approval = Approval.NONE
    params_schema = {
        "type": "object",
        "required": ["title"],
        "properties": {"title": {"type": "string"}, "description": {"type": "string"}},
    }

    async def execute(self, params: dict[str, Any], ctx: ActionContext) -> ActionResult:
        body = {
            "title": params["title"],
            "description": params.get("description", ""),
            "alertId": ctx.alert.id,
            "externalId": f"step-run-{ctx.step_run_id}",  # the mock returns the same ticket on a repeat
        }
        result = await self.call("POST", f"{self.base_url}/tickets", json=body)
        if result.ok:
            body = result.output.get("body")
            ticket_id = body.get("id") if isinstance(body, dict) else None
            return ActionResult(ok=True, output={**result.output, "ticketId": ticket_id})
        return result


# --- internal actions ----------------------------------------------------------------------------------


@dataclass
class DbAction:
    """Runs its SQL in its own short transaction on a fresh connection (actions run outside the executor's)."""

    conninfo: str
    now: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))

    def run_sync(self, fn: Callable[[psycopg.Connection], ActionResult]) -> ActionResult:
        with psycopg.connect(self.conninfo) as conn:
            return fn(conn)

    async def run(self, fn: Callable[[psycopg.Connection], ActionResult]) -> ActionResult:
        return await asyncio.to_thread(self.run_sync, fn)


class Escalate(DbAction):
    """Adds the team to the alert and notifies its on-call person (spec §7)."""

    name = "escalate"
    min_approval = Approval.NONE
    params_schema = {"type": "object", "required": ["team"], "properties": {"team": {"type": "string"}}}

    async def execute(self, params: dict[str, Any], ctx: ActionContext) -> ActionResult:
        team = params["team"]

        def work(conn: psycopg.Connection) -> ActionResult:
            if conn.execute("SELECT 1 FROM teams WHERE id = %s", (team,)).fetchone() is None:
                return ActionResult(ok=False, error=f"unknown team {team!r}")
            conn.execute(
                "INSERT INTO alert_escalations (alert_id, team_id, step_run_id) VALUES (%s, %s, %s)"
                " ON CONFLICT (alert_id, team_id) DO NOTHING",
                (ctx.alert.id, team, ctx.step_run_id),
            )
            # the feed shows the alert to the added team too (alerts.extra_teams, read by the API)
            added = conn.execute(
                "UPDATE alerts SET extra_teams = array_append(extra_teams, %s)"
                " WHERE id = %s AND NOT (%s = ANY(extra_teams))",
                (team, ctx.alert.id, team),
            ).rowcount
            if added:
                notify_alerts_changed(conn, [ctx.alert.id])
            planned = NotificationPlanner()(
                conn,
                AlertForPlanning(
                    id=ctx.alert.id,
                    rule_id=f"runbook:{ctx.runbook_id}",
                    severity=Severity(ctx.alert.severity or "critical"),
                    first_event_id=f"escalate:{ctx.step_run_id}",  # idempotent on repeat
                ),
                RuleForPlanning(id=f"runbook:{ctx.runbook_id}", owner={"team": team}, route=[{"to": {"oncall": team}}]),
                self.now(),
            )
            notified = sorted({n.user_id for n in planned.notifications})
            return ActionResult(ok=True, output={"team": team, "notified": notified})

        return await self.run(work)


class Notify(DbAction):
    """Sends a message about the alert to a recipient (same format as rule routes)."""

    name = "notify"
    min_approval = Approval.NONE
    params_schema = {
        "type": "object",
        "required": ["to", "message"],
        "properties": {
            "to": {"type": "object", "title": "Recipient: {user}, {team}, {oncall} or {team, role}"},
            "message": {"type": "string"},
            "channels": {"type": "array", "items": {"enum": ["ui", "email", "sms", "phone"]}},
        },
    }

    async def execute(self, params: dict[str, Any], ctx: ActionContext) -> ActionResult:
        entry: dict[str, Any] = {"to": params["to"]}
        if params.get("channels"):
            entry["channels"] = params["channels"]
        key = f"notify:{ctx.step_run_id}"

        def work(conn: psycopg.Connection) -> ActionResult:
            planned = NotificationPlanner()(
                conn,
                AlertForPlanning(
                    id=ctx.alert.id,
                    rule_id=f"runbook:{ctx.runbook_id}",
                    severity=Severity(ctx.alert.severity or "info"),
                    first_event_id=key,
                ),
                RuleForPlanning(id=f"runbook:{ctx.runbook_id}", owner=ctx.alert.owner, route=[entry]),
                self.now(),
            )
            conn.execute(
                "UPDATE notifications SET message = %s WHERE dedup_key = %s AND kind = 'alert'",
                (params["message"], key),
            )
            return ActionResult(ok=True, output={"notified": sorted({n.user_id for n in planned.notifications})})

        return await self.run(work)


@dataclass(frozen=True)
class ActionSettings:
    workflow_engine_url: str = "http://workflow-engine"
    orchestrator_url: str = "http://orchestrator"
    tickets_url: str = "http://tickets"


def build_registry(
    conninfo: str, settings: ActionSettings = ActionSettings(), transport: httpx.AsyncBaseTransport | None = None
) -> dict[str, Any]:
    actions = [
        HttpGet(transport),
        Webhook(transport),
        RetryWorkflow(settings.workflow_engine_url, transport),
        RestartService(settings.orchestrator_url, transport),
        OpenTicket(settings.tickets_url, transport),
        Escalate(conninfo),
        Notify(conninfo),
    ]
    return {action.name: action for action in actions}
