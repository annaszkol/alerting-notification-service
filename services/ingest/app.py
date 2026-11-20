"""Placeholder for the ingest service (owner: A) - HTTP / Kafka / log-tailer adapters (spec §2).

Replaced by the real implementation on its feature branch.
"""

from fastapi import FastAPI

app = FastAPI(title="ingest")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "ingest"}
