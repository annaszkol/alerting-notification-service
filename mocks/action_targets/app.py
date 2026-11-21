"""Mock targets of runbook actions (owner B, spec §2, §7). One app, reachable in compose under several host names:
counterparty-service, workflow-engine, orchestrator, tickets, webhook-receiver.

  GET  /health                          counterparty-service health check (200, or 503 when failing)
  POST /workflows/{id}/retry            workflow engine retry (200, or 500 when failing)
  POST /services/{name}/restart         orchestrator restart
  POST /tickets {title, externalId}     ticket system; the same externalId returns the same ticket
  POST /webhook                         webhook receiver

Failure modes for demos and tests (spec §13 test 12 "mock set to fail"):
  POST /api/fail {"target": "workflow", "times": 2}   fail the next 2 calls (times: -1 = always, 0 = never)
  GET  /api/state, GET /api/calls, DELETE /api/calls, GET / (page)
"""

from __future__ import annotations

import itertools
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import Body, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

Target = Literal["health", "workflow", "restart", "tickets", "webhook"]
TARGETS: tuple[str, ...] = ("health", "workflow", "restart", "tickets", "webhook")


class FailRule(BaseModel):
    target: Target
    times: int  # -1 always, 0 never, n > 0 the next n calls


def create_app() -> FastAPI:
    app = FastAPI(title="action-targets")
    page = (Path(__file__).parent / "index.html").read_text(encoding="utf-8")
    lock = threading.Lock()
    failing: dict[str, int] = dict.fromkeys(TARGETS, 0)
    calls: list[dict[str, Any]] = []
    tickets: dict[str, dict[str, Any]] = {}
    ids = itertools.count(1)

    def record(target: str, request: Request, status: int, body: Any = None) -> None:
        calls.append({
            "at": datetime.now(UTC).isoformat(timespec="milliseconds"), "target": target,
            "method": request.method, "path": request.url.path, "status": status, "body": body,
        })  # fmt: skip

    def should_fail(target: str) -> bool:
        n = failing[target]
        if n > 0:
            failing[target] = n - 1
        return n != 0

    def respond(target: str, request: Request, ok: dict[str, Any], fail_status: int, body: Any = None):
        with lock:
            failed = should_fail(target)
            status = fail_status if failed else 200
            record(target, request, status, body)
        content = {"error": f"{target} failing (mock)"} if failed else ok
        return JSONResponse(content, status_code=status)

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return page

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "service": "action-targets"}

    @app.get("/health")
    def health(request: Request):
        return respond("health", request, {"status": "UP"}, 503)

    @app.post("/workflows/{workflow_id}/retry")
    def retry(workflow_id: str, request: Request, body: Annotated[dict[str, Any] | None, Body()] = None):
        return respond("workflow", request, {"workflowId": workflow_id, "status": "RETRIED"}, 500, body)

    @app.post("/services/{service}/restart")
    def restart(service: str, request: Request):
        return respond("restart", request, {"service": service, "status": "RESTARTING"}, 500)

    @app.post("/tickets")
    def open_ticket(request: Request, body: Annotated[dict[str, Any], Body()]):
        with lock:
            if should_fail("tickets"):
                record("tickets", request, 500, body)
                return JSONResponse({"error": "tickets failing (mock)"}, status_code=500)
            key = str(body.get("externalId") or next(ids))
            ticket = tickets.setdefault(key, {"id": f"TCK-{next(ids)}", "title": body.get("title")})
            record("tickets", request, 200, body)
            return ticket

    @app.post("/webhook")
    def webhook(request: Request, body: Annotated[Any, Body()] = None):
        return respond("webhook", request, {"received": True}, 500, body)

    @app.post("/api/fail")
    def set_fail(rule: FailRule) -> dict[str, int]:
        with lock:
            failing[rule.target] = rule.times
            return dict(failing)

    @app.get("/api/state")
    def state() -> dict[str, int]:
        return dict(failing)

    @app.get("/api/calls")
    def list_calls() -> list[dict[str, Any]]:
        with lock:
            return list(calls)

    @app.delete("/api/calls", status_code=204)
    def clear() -> None:
        with lock:
            calls.clear()

    return app


app = create_app()
