import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from app import state


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "PATH", tmp_path / "agent.sqlite3")
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "secret-verify")
    monkeypatch.setenv("WHATSAPP_ALLOWED_NUMBERS", "5511999999999:aurora:g06")
    monkeypatch.delenv("WHATSAPP_APP_SECRET", raising=False)
    from app import main
    sent, handled = [], []

    async def fake_send(to, body):
        sent.append((to, body))

    async def fake_handle(school, guardian, name, content):
        handled.append((school, guardian, content))
        return {"status": "answered", "reply": "Resposta"}
    monkeypatch.setattr(main.whatsapp, "send_text", fake_send)
    monkeypatch.setattr(main, "handle_incoming", fake_handle)
    with TestClient(main.app) as test_client:
        test_client.sent, test_client.handled = sent, handled
        yield test_client


def payload(wamid="wamid.1", sender="5511999999999", **message):
    msg = {"from": sender, "id": wamid, "type": "text", "text": {"body": "nota?"}, **message}
    return {"entry": [{"changes": [{"value": {"messages": [msg]}}]}]}


def test_verify_handshake(client):
    ok = client.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "secret-verify", "hub.challenge": "123"})
    assert ok.status_code == 200 and ok.text == "123"
    bad = client.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "123"})
    assert bad.status_code == 403


def test_inbound_text_is_answered_over_whatsapp(client):
    assert client.post("/webhooks/whatsapp", json=payload()).status_code == 200
    assert client.handled == [("aurora", "g06", "nota?")]
    assert client.sent == [("5511999999999", "Resposta")]


def test_duplicate_wamid_is_processed_once(client):
    client.post("/webhooks/whatsapp", json=payload())
    client.post("/webhooks/whatsapp", json=payload())
    assert len(client.handled) == 1


def test_unmapped_sender_is_ignored(client):
    client.post("/webhooks/whatsapp", json=payload(sender="5500000000000"))
    assert client.handled == [] and client.sent == []


def test_non_text_gets_a_polite_reply(client):
    client.post("/webhooks/whatsapp", json=payload(type="image"))
    assert client.handled == []
    assert "texto" in client.sent[0][1]


def test_signature_enforced_when_app_secret_set(client, monkeypatch):
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "appsecret")
    body = json.dumps(payload()).encode()
    assert client.post("/webhooks/whatsapp", content=body, headers={"X-Hub-Signature-256": "sha256=bad"}).status_code == 403
    good = "sha256=" + hmac.new(b"appsecret", body, hashlib.sha256).hexdigest()
    assert client.post("/webhooks/whatsapp", content=body, headers={"X-Hub-Signature-256": good}).status_code == 200
    assert len(client.handled) == 1


def test_human_chatwoot_reply_is_forwarded_to_whatsapp(client):
    state.save_conversation("aurora", "g06", 4, 9, "human")
    client.post("/webhooks/chatwoot", json={"event": "message_created", "message_type": "outgoing", "private": False,
                                            "id": 77, "content": "Olá!", "conversation": {"id": 4}})
    assert client.sent == [("5511999999999", "Olá!")]
