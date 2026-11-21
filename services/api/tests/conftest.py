"""API test fixtures: throwaway database (all migrations), seed users, client."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from db.testing import TEST_DATABASE_URL, temporary_database
from services.api.app import create_app
from services.api.db import create_pool
from services.api.settings import Settings

SECRET = "test-secret-at-least-32-bytes-long-0123456789"

# id, name, is_admin, {team: role}
USERS = [
    ("admin", "Admin", True, {}),
    ("marek", "Marek Manager", False, {"rates": "manager"}),
    ("mia", "Mia Member", False, {"rates": "member", "pricing": "member"}),
    ("anna", "Anna", False, {"payments": "member"}),
    ("piotr", "Piotr Pricing", False, {"pricing": "manager"}),
]
TEAMS = ["rates", "pricing", "payments", "settlements"]


def pytest_collection_modifyitems(items):
    for item in items:
        if "client" in item.fixturenames or "conn" in item.fixturenames:
            item.add_marker(pytest.mark.db)


@pytest.fixture(scope="session")
def database() -> Iterator[str]:
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is not set")
    with temporary_database("test_api", migrate=True) as conninfo:
        with psycopg.connect(conninfo) as conn:
            for team in TEAMS:
                conn.execute("INSERT INTO teams (id, name) VALUES (%s, %s)", (team, team.title()))
            for user_id, name, is_admin, teams in USERS:
                conn.execute(
                    "INSERT INTO users (id, name, email, timezone, is_admin) VALUES (%s, %s, %s, %s, %s)",
                    (user_id, name, f"{user_id}@example.test", "Europe/Warsaw", is_admin),
                )
                for team, role in teams.items():
                    conn.execute(
                        "INSERT INTO team_members (team_id, user_id, role) VALUES (%s, %s, %s)",
                        (team, user_id, role),
                    )
        yield conninfo


@pytest.fixture
def settings(database: str) -> Settings:
    return Settings(database_url=database, jwt_secret=SECRET, public_base_url="http://ui.test")


@pytest.fixture
def conn(database: str) -> Iterator[psycopg.Connection]:
    """Autocommit connection for arranging data and checking results; clean tables before each test."""
    with psycopg.connect(database, autocommit=True, row_factory=dict_row) as c:
        c.execute("TRUNCATE alerts, rules, notifications, audit_log RESTART IDENTITY")
        c.execute("UPDATE config_version SET version = 1")
        yield c


@pytest.fixture
def client(settings: Settings, conn: psycopg.Connection) -> Iterator[TestClient]:
    with TestClient(create_app(settings, create_pool(settings.database_url))) as c:
        yield c


@pytest.fixture
def login(client: TestClient):
    def _login(user_id: str) -> dict[str, str]:
        response = client.post("/api/auth/login", json={"userId": user_id})
        assert response.status_code == 200, response.text
        return {"Authorization": f"Bearer {response.json()['token']}"}

    return _login


@pytest.fixture
def listener(database: str):
    """Collects NOTIFY payloads on a channel after the code under test committed."""
    conns: list[psycopg.Connection] = []

    def _listen(channel: str):
        c = psycopg.connect(database, autocommit=True)
        c.execute(f"LISTEN {channel}")
        conns.append(c)

        def received(timeout: float = 1.0) -> list[Any]:
            return [json.loads(n.payload) for n in c.notifies(timeout=timeout, stop_after=50)]

        return received

    yield _listen
    for c in conns:
        c.close()
