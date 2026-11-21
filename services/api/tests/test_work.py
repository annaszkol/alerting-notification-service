"""Work time start / stop (spec §6 "Czas pracy"; contracts/openapi.yaml tag `work`)."""

import pytest

from services.api.tests.helpers import assert_matches


@pytest.fixture
def clean(conn):
    conn.execute("TRUNCATE work_sessions, work_tracking")
    yield conn


def test_start_stop_and_status(client, login, clean):
    anna = login("anna")
    status = client.get("/api/me/work", headers=anna).json()
    assert_matches("WorkStatus", status)
    assert status["tracking"] is False and status["active"] is False

    started = client.post("/api/me/work/start", headers=anna)
    assert started.status_code == 200
    body = started.json()
    assert_matches("WorkStatus", body)
    assert body["tracking"] and body["active"] and body["startedAt"] and body["autoStopAt"]

    again = client.post("/api/me/work/start", headers=anna).json()  # twice = still the same session
    assert again["startedAt"] == body["startedAt"]
    assert len(again["recent"]) == 1

    at_work = client.get("/api/work/active", headers=login("mia")).json()
    assert [p["userId"] for p in at_work] == ["anna"]

    stopped = client.post("/api/me/work/stop", headers=anna).json()
    assert stopped["active"] is False and stopped["tracking"] is True
    assert stopped["recent"][0]["endedHow"] == "user"
    assert client.post("/api/me/work/stop", headers=anna).status_code == 409
    assert client.get("/api/work/active", headers=anna).json() == []


def test_tracking_can_be_switched_off_and_each_person_has_their_own(client, login, clean):
    client.post("/api/me/work/start", headers=login("anna"))
    off = client.put("/api/me/work", json={"tracking": False}, headers=login("anna")).json()
    assert off["tracking"] is False
    assert client.get("/api/work/active", headers=login("anna")).json() == []  # not tracking: not listed
    assert client.get("/api/me/work", headers=login("mia")).json()["active"] is False
    assert client.put("/api/me/work", json={"tracking": "yes"}, headers=login("anna")).status_code == 422


def test_needs_login(client):
    assert client.post("/api/me/work/start").status_code == 401
