"""Alert feed, details, timeline, ack / resolve (spec §5 lifecycle, §6 dead banner, §9 P1)."""

from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from services.api.domain.alerts import ack_alert
from services.api.tests.helpers import assert_matches, insert_alert

T0 = datetime(2026, 11, 21, 9, 0, tzinfo=UTC)


def at(minutes: int) -> datetime:
    return T0 + timedelta(minutes=minutes)


# --- feed ----------------------------------------------------------------------------------------------


def test_feed_needs_login(client):
    assert client.get("/api/alerts").status_code == 401


def test_feed_defaults_to_open_and_acknowledged_newest_first(client, login, conn):
    a = insert_alert(conn, title="a", last_seen=at(1))
    b = insert_alert(conn, title="b", last_seen=at(3), state="acknowledged")
    insert_alert(conn, title="c", last_seen=at(5), state="resolved")
    response = client.get("/api/alerts", headers=login("mia"))
    assert response.status_code == 200
    page = response.json()
    assert [x["id"] for x in page["items"]] == [b, a]
    assert page["nextCursor"] is None
    for item in page["items"]:
        assert_matches("AlertSummary", item)


def test_feed_filters(client, login, conn):
    rates = insert_alert(conn, title="r", owner={"team": "rates"}, severity="error")
    escalated = insert_alert(conn, title="e", owner={"team": "settlements"}, extra_teams=["rates"])
    personal = insert_alert(conn, title="p", owner={"user": "anna"}, severity="warning")
    resolved = insert_alert(conn, title="old", owner={"team": "rates"}, state="resolved")
    headers = login("mia")

    def ids(**params):
        return {x["id"] for x in client.get("/api/alerts", params=params, headers=headers).json()["items"]}

    assert ids(team="rates") == {rates, escalated}
    assert ids(user="anna") == {personal}
    assert ids(severity=["error", "warning"]) == {rates, personal}
    assert ids(state=["resolved"]) == {resolved}
    assert ids(team="rates", state=["open", "resolved"]) == {rates, escalated, resolved}
    assert ids(q="OLD", state=["resolved"]) == {resolved}
    assert ids(q="%") == set()  # LIKE wildcards are escaped


def test_escalated_team_is_listed_in_teams(client, login, conn):
    alert_id = insert_alert(conn, owner={"team": "settlements"}, extra_teams=["rates", "settlements"])
    item = client.get(f"/api/alerts/{alert_id}", headers=login("mia")).json()
    assert item["teams"] == ["settlements", "rates"]


def test_feed_pagination_is_stable(client, login, conn):
    created = [insert_alert(conn, title=f"a{i}", last_seen=at(i % 3)) for i in range(7)]
    headers = login("mia")
    seen, cursor = [], None
    while True:
        params = {"limit": 3, **({"cursor": cursor} if cursor else {})}
        page = client.get("/api/alerts", params=params, headers=headers).json()
        seen += [x["id"] for x in page["items"]]
        cursor = page["nextCursor"]
        if not cursor:
            break
    assert sorted(seen) == sorted(created) and len(seen) == len(set(seen))


@pytest.mark.parametrize("params", [{"cursor": "garbage"}, {"limit": 0}, {"severity": "fatal"}, {"team": "A B"}])
def test_feed_rejects_bad_parameters(client, login, params):
    response = client.get("/api/alerts", params=params, headers=login("mia"))
    assert response.status_code == 422
    assert_matches("Error", response.json())


def test_live_fields_are_rendered_on_read(client, login, conn):
    alert_id = insert_alert(conn, title="{{$count}} warnings ({{$state}}) {{$alertUrl}}", count=17)
    item = client.get("/api/alerts", headers=login("mia")).json()["items"][0]
    assert item["id"] == alert_id
    assert item["title"] == f"17 warnings (open) http://ui.test/a/{alert_id}"


# --- details -------------------------------------------------------------------------------------------


def test_detail_shows_fields_notifications_and_dead_banner(client, login, conn):
    alert_id = insert_alert(
        conn,
        fields=["step", "error.message", 'labels["app.kubernetes.io/name"]', "$service", "missing.path"],
        body="Step {{$count}}",
    )
    conn.execute(
        "INSERT INTO notifications (alert_id, user_id, channel, dedup_key, status, attempts, last_error)"
        " VALUES (%s, 'anna', 'sms', 'evt-1', 'dead', 10, 'gateway down'),"
        "        (%s, 'anna', 'phone', 'evt-1', 'sent', 1, NULL)",
        (alert_id, alert_id),
    )
    response = client.get(f"/api/alerts/{alert_id}", headers=login("mia"))
    assert response.status_code == 200
    detail = response.json()
    assert_matches("AlertDetail", detail)
    assert detail["fields"] == [
        {"path": "step", "value": "confirm"},
        {"path": "error.message", "value": "counterparty timeout"},
        {"path": 'labels["app.kubernetes.io/name"]', "value": "settlement"},
        {"path": "$service", "value": "order-settlement"},
        {"path": "missing.path", "value": None},
    ]
    assert detail["body"] == "Step 1"
    assert detail["hasDeadNotification"] is True
    assert {(n["channel"], n["status"]) for n in detail["notifications"]} == {
        ("sms", "dead"),
        ("phone", "sent"),
    }


def test_unknown_alert_is_404(client, login):
    headers = login("mia")
    assert client.get("/api/alerts/999", headers=headers).status_code == 404
    assert client.get("/api/alerts/999/timeline", headers=headers).status_code == 404
    assert client.post("/api/alerts/999/ack", headers=headers).status_code == 404
    assert client.post("/api/alerts/999/resolve", headers=headers).status_code == 404


# --- ack / resolve -------------------------------------------------------------------------------------


def test_ack_changes_state_writes_audit_and_notifies(client, login, conn, listener):
    alert_id = insert_alert(conn)
    received = listener("alerts_changed")
    response = client.post(f"/api/alerts/{alert_id}/ack", headers=login("mia"))
    assert response.status_code == 200
    assert_matches("AlertSummary", response.json())
    assert response.json()["state"] == "acknowledged"
    row = conn.execute("SELECT state, acked_by, acked_at FROM alerts WHERE id = %s", (alert_id,)).fetchone()
    assert row["state"] == "acknowledged" and row["acked_by"] == "mia" and row["acked_at"] is not None
    audit = conn.execute("SELECT actor, action, target_id, details FROM audit_log").fetchall()
    assert audit == [
        {"actor": "mia", "action": "alert.acknowledged", "target_id": str(alert_id), "details": {"via": "ui"}}
    ]
    assert received() == [{"ids": [alert_id]}]


def test_ack_twice_is_409(client, login, conn):
    alert_id = insert_alert(conn)
    headers = login("mia")
    client.post(f"/api/alerts/{alert_id}/ack", headers=headers)
    response = client.post(f"/api/alerts/{alert_id}/ack", headers=headers)
    assert response.status_code == 409
    assert response.json()["error"]["details"] == {"state": "acknowledged"}


def test_resolve_with_note_from_acknowledged(client, login, conn):
    alert_id = insert_alert(conn, state="acknowledged")
    response = client.post(f"/api/alerts/{alert_id}/resolve", json={"note": "restarted"}, headers=login("anna"))
    assert response.status_code == 200 and response.json()["state"] == "resolved"
    detail = client.get(f"/api/alerts/{alert_id}", headers=login("anna")).json()
    assert detail["resolvedBy"] == "anna"
    assert detail["resolvedHow"] == "manual"
    assert detail["resolveNote"] == "restarted"


def test_resolve_without_body(client, login, conn):
    alert_id = insert_alert(conn)
    assert client.post(f"/api/alerts/{alert_id}/resolve", headers=login("anna")).status_code == 200


def test_resolved_alert_cannot_be_acked_or_resolved(client, login, conn):
    alert_id = insert_alert(conn, state="resolved")
    headers = login("mia")
    assert client.post(f"/api/alerts/{alert_id}/ack", headers=headers).status_code == 409
    assert client.post(f"/api/alerts/{alert_id}/resolve", headers=headers).status_code == 409


def test_timeline_merges_audit_and_notifications(client, login, conn):
    alert_id = insert_alert(conn)
    conn.execute(
        "INSERT INTO notifications (alert_id, user_id, channel, dedup_key, status, created_at)"
        " VALUES (%s, 'anna', 'sms', 'evt-1', 'pending', now() - interval '1 minute')",
        (alert_id,),
    )
    headers = login("mia")
    client.post(f"/api/alerts/{alert_id}/ack", headers=headers)
    client.post(f"/api/alerts/{alert_id}/resolve", json={"note": "done"}, headers=headers)
    timeline = client.get(f"/api/alerts/{alert_id}/timeline", headers=headers).json()
    for entry in timeline:
        assert_matches("TimelineEntry", entry)
    assert [e["summary"] for e in timeline] == [
        "alert to anna via sms: pending",
        "acknowledged by mia via ui",
        "resolved by mia via ui: done",
    ]


# --- shared function (used by B's inbound SMS handler) -------------------------------------------------


def test_ack_function_runs_in_callers_transaction(database, conn, listener):
    alert_id = insert_alert(conn)
    received = listener("alerts_changed")
    with psycopg.connect(database) as tx:  # tuple rows, like a caller without dict_row
        ack_alert(tx, alert_id, "anna", via="sms")
        tx.rollback()
    assert conn.execute("SELECT state FROM alerts WHERE id = %s", (alert_id,)).fetchone()["state"] == "open"
    assert conn.execute("SELECT count(*) AS n FROM audit_log").fetchone()["n"] == 0
    assert received(timeout=0.5) == []

    with psycopg.connect(database) as tx:
        ack_alert(tx, alert_id, "anna", via="sms")
    assert received() == [{"ids": [alert_id]}]
    details = conn.execute("SELECT details FROM audit_log").fetchone()["details"]
    assert details == {"via": "sms"}


def test_audit_log_is_append_only(conn):
    conn.execute("INSERT INTO audit_log (actor, action, target_type, target_id) VALUES ('x', 'y', 'alert', '1')")
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("UPDATE audit_log SET actor = 'z'")
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("DELETE FROM audit_log")
