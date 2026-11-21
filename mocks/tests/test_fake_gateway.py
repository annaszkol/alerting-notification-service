import json

import httpx
import pytest
from fastapi.testclient import TestClient

from mocks.fake_gateway.app import create_app

INBOUND = "http://api.test/api/inbound/sms"


@pytest.fixture
def inbound_calls():
    return []


@pytest.fixture
def client(inbound_calls):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        inbound_calls.append((str(request.url), body))
        result = "acknowledged" if body["text"].startswith("ACK") else "unknown_code"
        return httpx.Response(200, json={"result": result})

    return TestClient(create_app(INBOUND, transport=httpx.MockTransport(handler)))


def sms(key="n-1", text="[CRITICAL] Settlement failed /a/1 ACK K7Q2MX", code="K7Q2MX"):
    return {"to": "+48500100200", "text": text, "idempotencyKey": key, "replyCode": code}


def test_records_sms_with_receive_time(client):
    response = client.post("/sms", json=sms())
    assert response.status_code == 202
    [message] = client.get("/api/messages", params={"channel": "sms"}).json()
    assert message["to"] == "+48500100200"
    assert message["direction"] == "out"
    assert message["receivedAt"].endswith("Z")


def test_same_idempotency_key_is_recorded_once(client):
    assert client.post("/sms", json=sms(key="n-7")).status_code == 202
    second = client.post("/sms", json=sms(key="n-7"))
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    [message] = client.get("/api/messages").json()
    assert message["duplicates"] == 1


def test_sms_and_call_keys_are_separate(client):
    client.post("/sms", json=sms(key="n-1"))
    assert client.post("/calls", json=sms(key="n-1")).status_code == 202
    assert len(client.get("/api/messages").json()) == 2


def test_outage_returns_503_and_records_nothing(client):
    assert client.post("/api/outage", json={"sms": True}).json() == {"sms": True, "phone": False}
    assert client.post("/sms", json=sms()).status_code == 503
    assert client.post("/calls", json=sms()).status_code == 202  # phone still up
    assert client.get("/api/messages", params={"channel": "sms"}).json() == []

    client.post("/api/outage", json={"sms": False})
    assert client.post("/sms", json=sms()).status_code == 202


def test_reply_is_forwarded_to_api(client, inbound_calls):
    result = client.post("/api/reply", json={"from": "+48500100200", "text": "ACK K7Q2MX"}).json()
    assert result == {"result": "acknowledged"}
    [(url, body)] = inbound_calls
    assert url == INBOUND
    assert body["from"] == "+48500100200"
    assert body["text"] == "ACK K7Q2MX"
    assert {"receivedAt", "gatewayMessageId"} <= body.keys()
    [inbound] = client.get("/api/messages", params={"direction": "in"}).json()
    assert inbound["result"] == "acknowledged"


def test_press1_acks_from_the_callee_number(client, inbound_calls):
    call_id = client.post("/calls", json=sms(key="n-2")).json()["id"]
    assert client.post(f"/api/calls/{call_id}/press1").json() == {"result": "acknowledged"}
    assert inbound_calls[0][1]["from"] == "+48500100200"
    assert inbound_calls[0][1]["text"] == "ACK K7Q2MX"
    call = next(m for m in client.get("/api/messages").json() if m["id"] == call_id)
    assert call["pressed1"] == "acknowledged"


def test_press1_unknown_call(client):
    assert client.post("/api/calls/999/press1").status_code == 404


def test_api_down_is_reported_not_raised():
    def down(request):
        raise httpx.ConnectError("refused")

    client = TestClient(create_app(INBOUND, transport=httpx.MockTransport(down)))
    result = client.post("/api/reply", json={"from": "+48500100200", "text": "ACK X"}).json()
    assert result["result"] == "api_unreachable"


def test_clear_and_page(client):
    client.post("/sms", json=sms())
    assert client.delete("/api/messages").status_code == 204
    assert client.get("/api/messages").json() == []
    assert "Fake SMS / phone gateway" in client.get("/").text
