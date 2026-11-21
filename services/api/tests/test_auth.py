"""Mock login, JWT and /me (spec §9)."""

from datetime import UTC, datetime, timedelta

from services.api.auth import create_token
from services.api.settings import Settings
from services.api.tests.helpers import assert_matches


def test_login_users_are_public(client):
    response = client.get("/api/auth/users")
    assert response.status_code == 200
    users = response.json()
    assert {u["id"] for u in users} >= {"admin", "marek", "anna"}
    for user in users:
        assert_matches("User", user)


def test_login_returns_token_that_opens_me(client):
    response = client.post("/api/auth/login", json={"userId": "mia"})
    assert response.status_code == 200
    assert_matches("LoginResponse", response.json())
    token = response.json()["token"]

    me = client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert_matches("Me", me.json())
    assert me.json()["isAdmin"] is False
    assert {(t["id"], t["role"]) for t in me.json()["teams"]} == {("rates", "member"), ("pricing", "member")}


def test_login_unknown_user_is_404(client):
    response = client.post("/api/auth/login", json={"userId": "nobody"})
    assert response.status_code == 404
    assert_matches("Error", response.json())
    assert response.json()["error"]["code"] == "not_found"


def test_login_body_is_validated(client):
    response = client.post("/api/auth/login", json={"userId": "Anna!", "extra": 1})
    assert response.status_code == 422
    body = response.json()
    assert_matches("Error", body)
    assert body["error"]["code"] == "invalid"
    assert {d["path"] for d in body["error"]["details"]} == {"userId", "extra"}


def test_me_without_token_is_401(client):
    response = client.get("/api/me")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert response.headers["www-authenticate"] == "Bearer"


def test_token_signed_with_another_secret_is_401(client, settings):
    other = Settings(database_url=settings.database_url, jwt_secret="another-secret-also-32-bytes-long-xx")
    token, _ = create_token(other, "admin")
    response = client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json()["error"]["message"] == "invalid token"


def test_expired_token_is_401(client, settings):
    token, _ = create_token(settings, "admin", now=datetime.now(UTC) - timedelta(days=2))
    response = client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json()["error"]["message"] == "token expired"


def test_token_of_deleted_user_is_401(client, settings, conn):
    conn.execute("INSERT INTO users (id, name) VALUES ('ghost', 'Ghost')")
    token, _ = create_token(settings, "ghost")
    conn.execute("DELETE FROM users WHERE id = 'ghost'")
    response = client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_role_change_applies_without_new_token(client, login, conn):
    headers = login("anna")
    assert client.get("/api/me", headers=headers).json()["teams"] == [
        {"id": "payments", "name": "Payments", "role": "member"}
    ]
    conn.execute("UPDATE team_members SET role = 'manager' WHERE user_id = 'anna' AND team_id = 'payments'")
    try:
        assert client.get("/api/me", headers=headers).json()["teams"][0]["role"] == "manager"
    finally:
        conn.execute("UPDATE team_members SET role = 'member' WHERE user_id = 'anna' AND team_id = 'payments'")
