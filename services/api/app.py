"""API service (owner C): REST API under /api, auth, WebSocket (spec §9). Contract: contracts/openapi.yaml.

Run: `uvicorn services.api.app:app` (compose does this). `create_app()` takes settings and a pool for tests.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, WebSocket
from psycopg_pool import ConnectionPool

from services.api import errors
from services.api.db import create_pool
from services.api.routers import alerts, auth, inbound, rules
from services.api.settings import Settings
from services.api.ws import Hub

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def create_app(
    settings: Settings | None = None,
    pool: ConnectionPool | None = None,
    hub_options: dict[str, Any] | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    pool = pool or create_pool(settings.database_url)
    hub = Hub(settings.database_url, pool, settings.public_base_url, **(hub_options or {}))

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        pool.open(wait=True, timeout=30)
        await hub.start()
        try:
            yield
        finally:
            await hub.stop()
            pool.close()

    app = FastAPI(
        title="Alerting & Notification Service API",
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.settings = settings
    app.state.pool = pool
    app.state.hub = hub
    errors.install(app)

    for module in (auth, alerts, rules, inbound):
        app.include_router(module.router, prefix="/api")

    @app.websocket("/ws")
    async def websocket(ws: WebSocket) -> None:
        await hub.serve(ws, settings)

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok", "service": "api"}

    return app


app = create_app()
