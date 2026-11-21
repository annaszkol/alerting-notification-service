"""Placeholder for the api service (owner: C) - REST API, auth, WebSocket (spec §9).

Replaced by the real implementation on its feature branch.
"""

from fastapi import FastAPI

app = FastAPI(title="api")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "api"}
