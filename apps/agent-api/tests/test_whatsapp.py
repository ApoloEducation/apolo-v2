import hashlib
import hmac
import json

import httpx
import pytest

from app import main, whatsapp
from app.agent import Escalate
from app.state import conversation, history

from test_flow import FakeChatwoot


@pytest.fixture(autouse=True)
def wa_env(monkeypatch):
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "123")
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "verify-me")
    monkeypatch.delenv("WHATSAPP_APP_SECRET", raising=False)
    monkeypatch.setenv("WHATSAPP_GUARDIAN_PHONES", "5511999990001=g01,5511988880002=g02")


@pytest.fixture
def sent(monkeypatch):
    out = []

    async def fake_send(phone, text):
        out.append((phone, text))
    monkeypatch.setattr(whatsapp, "send_text", fake_send)
    monkeypatch.setattr(main, "chatwoot", FakeChatwoot())

    async def name(_gid):
        return "Marina Almeida"
    monkeypatch.setattr(main, "guardian_name", name)
    return out


@pytest.fixture
def client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test")


def inbound(phone="5511999990001", text="Qual a nota de Matemática?", wamid="wamid.1", kind="text"):
    msg = {"from": phone, "id": wamid, "type": kind}
    if kind == "text":
        msg["text"] = {"body": text}
    return {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {
        "contacts": [{"wa_id": phone, "profile": {"name": "Mari"}}], "messages": [msg]}}]}]}


def test_verification_handshake():
    from fastapi.testclient import TestClient
    c = TestClient(main.app)
    ok = c.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "42"})
    assert ok.status_code == 200 and ok.text == "42"
    assert c.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "bad", "hub.challenge": "1"}).status_code == 403


def test_brazilian_ninth_digit_variants_match():
    assert whatsapp.guardian_for_phone("551199990001") == "g01"      # Meta's spelling without the 9
    assert whatsapp.guardian_for_phone("+55 (11) 98888-0002") == "g02"
    assert whatsapp.guardian_for_phone("5511977770003") is None


@pytest.mark.anyio
async def test_known_parent_gets_agent_reply_on_whatsapp(client, sent, monkeypatch):
    async def fake_answer(gid, text, _history):
        assert gid == "g01"
        return "A nota é 7,1."
    monkeypatch.setattr(main, "answer", fake_answer)
    assert (await client.post("/webhooks/whatsapp", json=inbound())).json() == {"ok": True}
    assert sent == [("5511999990001", "A nota é 7,1.")]
    assert [h["sender"] for h in history("g01")] == ["guardian", "agent"]


@pytest.mark.anyio
async def test_duplicate_delivery_is_ignored(client, sent, monkeypatch):
    async def fake_answer(*_a, **_k):
        return "ok"
    monkeypatch.setattr(main, "answer", fake_answer)
    await client.post("/webhooks/whatsapp", json=inbound(wamid="wamid.dup"))
    await client.post("/webhooks/whatsapp", json=inbound(wamid="wamid.dup"))
    assert len(sent) == 1


@pytest.mark.anyio
async def test_unknown_number_is_told_and_never_reaches_agent(client, sent, monkeypatch):
    async def fail(*_a, **_k):
        raise AssertionError("agent must not run")
    monkeypatch.setattr(main, "answer", fail)
    await client.post("/webhooks/whatsapp", json=inbound(phone="5511900000000"))
    assert sent == [("5511900000000", whatsapp.UNKNOWN_NUMBER)]


@pytest.mark.anyio
async def test_non_text_message_gets_hint(client, sent):
    await client.post("/webhooks/whatsapp", json=inbound(kind="image"))
    assert sent == [("5511999990001", whatsapp.UNSUPPORTED)]


@pytest.mark.anyio
async def test_handoff_and_human_reply_reach_whatsapp(client, sent, monkeypatch):
    async def boom(*_a, **_k):
        raise Escalate("x", "financeiro", "q")
    monkeypatch.setattr(main, "answer", boom)
    await client.post("/webhooks/whatsapp", json=inbound())
    assert conversation("g01")["mode"] == "human"
    assert sent == [("5511999990001", main.HANDOFF)]
    await client.post("/webhooks/chatwoot", json={"event": "message_created", "message_type": "outgoing", "id": 900,
                      "content": "Olá!", "conversation": {"id": 10}, "sender": {"name": "Pedro"}})
    assert sent[-1] == ("5511999990001", "*Pedro:* Olá!")


@pytest.mark.anyio
async def test_signature_enforced_when_secret_set(client, sent, monkeypatch):
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "s3cret")
    body = json.dumps(inbound()).encode()
    assert (await client.post("/webhooks/whatsapp", content=body)).status_code == 403
    sig = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    ok = await client.post("/webhooks/whatsapp", content=body, headers={"X-Hub-Signature-256": sig})
    assert ok.status_code == 200
