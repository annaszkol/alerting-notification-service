"""WebSocket push after LISTEN alerts_changed (spec §2, contracts/ws-messages.md, contracts/notify.md)."""

from contextlib import contextmanager

import psycopg
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from contracts.notify import notify_alerts_changed
from services.api import schemas
from services.api.app import create_app
from services.api.db import create_pool
from services.api.tests.helpers import insert_alert

SERVER = schemas.Draft202012Validator(
    {"$ref": f"{schemas.BASE}ws-messages.schema.json#/$defs/serverMessage"}, registry=schemas.registry()
)


def token_for(client: TestClient, user: str = "mia") -> str:
    return client.post("/api/auth/login", json={"userId": user}).json()["token"]


@contextmanager
def connect(client: TestClient, user: str = "mia", filters: dict | None = None):
    """Connected and subscribed session (waits for `subscribed`, so filters are in force)."""
    with client.websocket_connect(f"/ws?token={token_for(client, user)}") as session:
        session.send_json({"type": "subscribe", **({"filters": filters} if filters is not None else {})})
        ack = session.receive_json()
        assert ack["type"] == "subscribed", ack
        SERVER.validate(ack)
        yield session


def receive(session) -> dict:
    message = session.receive_json()
    SERVER.validate(message)
    return message


def changed(conn: psycopg.Connection, **values) -> int:
    alert_id = insert_alert(conn, **values)
    notify_alerts_changed(conn, [alert_id])
    return alert_id


# --- auth ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("query", ["", "?token=garbage"])
def test_invalid_token_closes_with_4401(client, query):
    with client.websocket_connect(f"/ws{query}") as ws:
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
    assert closed.value.code == 4401


# --- delivery ------------------------------------------------------------------------------------------


def test_upsert_arrives_after_commit_with_live_title(client, conn):
    with connect(client) as session:
        alert_id = changed(conn, title="{{$count}} rejections", count=4)
        message = receive(session)
    assert message["type"] == "alert.upsert"
    assert message["alert"]["id"] == alert_id
    assert message["alert"]["title"] == "4 rejections"


def test_rolled_back_change_is_never_pushed(client, conn, database):
    with connect(client) as session:
        with psycopg.connect(database) as tx:
            ghost = insert_alert(tx, title="ghost")
            notify_alerts_changed(tx, [ghost])
            tx.rollback()
        real = changed(conn, title="real")
        message = receive(session)  # the first message is the committed one
    assert message["alert"]["id"] == real


def test_team_filter(client, conn):
    with connect(client, filters={"team": "rates"}) as session:
        changed(conn, title="other", owner={"team": "settlements"})
        rates = changed(conn, title="mine", owner={"team": "rates"})
        assert receive(session)["alert"]["id"] == rates


def test_severity_filter_and_escalated_team(client, conn):
    with connect(client, filters={"team": "rates", "severity": ["critical"]}) as session:
        changed(conn, title="low", owner={"team": "rates"}, severity="info")
        escalated = changed(conn, title="esc", owner={"team": "settlements"}, extra_teams=["rates"])
        assert receive(session)["alert"]["id"] == escalated


def test_state_change_outside_state_filter_still_arrives(client, conn, login):
    alert_id = insert_alert(conn)
    with connect(client, filters={"state": ["open"]}) as session:
        assert client.post(f"/api/alerts/{alert_id}/ack", headers=login("mia")).status_code == 200
        message = receive(session)
    assert message["alert"]["id"] == alert_id
    assert message["alert"]["state"] == "acknowledged"


def test_many_ids_across_several_notifies_all_arrive(client, conn):
    with connect(client) as session:
        ids = [insert_alert(conn, title=f"a{i}") for i in range(600)]
        notify_alerts_changed(conn, ids)  # 2 NOTIFY messages (350 + 250)
        received = {receive(session)["alert"]["id"] for _ in ids}
    assert received == set(ids)


def test_slow_consumer_gets_resync(settings, conn, login):
    app = create_app(settings, create_pool(settings.database_url), hub_options={"queue_size": 3})
    with TestClient(app) as client:
        with connect(client) as session:
            ids = [insert_alert(conn, title=f"s{i}") for i in range(20)]
            notify_alerts_changed(conn, ids)
            messages = []
            while not messages or messages[-1]["type"] != "resync":
                messages.append(receive(session))
    assert messages[-1] == {"type": "resync", "reason": "slow_consumer"}


def test_malformed_messages_are_ignored(client):
    with client.websocket_connect(f"/ws?token={token_for(client)}") as ws:
        ws.send_text("not json")
        ws.send_json({"type": "subscribe", "filters": {"severity": ["fatal"]}})
        ws.send_json({"type": "hello"})
        ws.send_json({"type": "subscribe", "filters": {"team": "rates"}})
        assert ws.receive_json() == {
            "type": "subscribed",
            "filters": {"team": "rates", "state": ["open", "acknowledged"]},
        }


# --- keepalive and reconnect ---------------------------------------------------------------------------


def test_ping_and_disconnect_without_pong(settings):
    app = create_app(settings, create_pool(settings.database_url), hub_options={"ping_interval": 0.05})
    with TestClient(app) as client, client.websocket_connect(f"/ws?token={token_for(client)}") as ws:
        assert ws.receive_json() == {"type": "ping"}
        ws.send_json({"type": "pong"})
        pings = 0
        with pytest.raises(WebSocketDisconnect):
            while True:
                assert ws.receive_json() == {"type": "ping"}
                pings += 1
        assert pings >= 2  # closed after two unanswered pings


def test_listener_reconnect_sends_resync_and_keeps_working(client, conn):
    with connect(client) as session:
        killed = conn.execute(
            "SELECT pg_terminate_backend(pid) AS ok FROM pg_stat_activity"
            " WHERE datname = current_database() AND query ILIKE 'LISTEN%%'"
        ).fetchall()
        assert killed and all(r["ok"] for r in killed)
        assert receive(session) == {"type": "resync", "reason": "listener_reconnected"}
        alert_id = changed(conn, title="after reconnect")
        assert receive(session)["alert"]["id"] == alert_id
