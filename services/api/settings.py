"""API settings from environment variables (secrets only in env, AGENTS.md)."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

log = logging.getLogger("api")

# Used only when JWT_SECRET is unset, so `docker compose up` works without a .env file. Logged as a warning.
DEV_JWT_SECRET = "dev-only-insecure-jwt-secret-change-me"


@dataclass(frozen=True)
class Settings:
    database_url: str
    jwt_secret: str
    jwt_ttl_seconds: int = 12 * 3600
    public_base_url: str = "http://localhost:3000"

    @classmethod
    def from_env(cls) -> Settings:
        secret = os.environ.get("JWT_SECRET")
        if not secret:
            log.warning("JWT_SECRET is not set; using an insecure development secret")
            secret = DEV_JWT_SECRET
        return cls(
            database_url=os.environ.get("DATABASE_URL", "postgresql://alerting:alerting@localhost:5432/alerting"),
            jwt_secret=secret,
            jwt_ttl_seconds=int(os.environ.get("JWT_TTL_SECONDS", 12 * 3600)),
            public_base_url=os.environ.get("PUBLIC_BASE_URL", "http://localhost:3000").rstrip("/"),
        )
