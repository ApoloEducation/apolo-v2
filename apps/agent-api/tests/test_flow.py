import httpx
import pytest

from app import main
from app.agent import Escalate
from app.state import conversation, history

HANDOFF_TEXT = main.HANDOFF


class FakeChatwoot:
    def __init__(self):
        self.sent, self.escalations, self.next_id = [], [], 100
        self.on_message = None  # hook to simulate Chatwoot's webhook firing before we get the response

    async def ensure_contact_and_conversation(self, guardian_id, name, existing):
        return 1, 10

    async def message(self, conversation_id, content, message_type="incoming", bot=False):
        self.next_id += 1
        self.sent.append((message_type, content, bot))
        if self.on_message and message_type == "outgoing":
            await self.on_message(self.next_id, content, bot)
        return {"id": self.next_id}

    async def escalate(self, conversation_id, category="outros", reason="", question=""):
        self.escalations.append((category, reason, question))

    async def delete_conversation_and_contact(self, conversation_id, contact_id):
        self.deleted = getattr(self, "deleted", []) + [(conversation_id, contact_id)]
        return []


@pytest.fixture
def fake(monkeypatch):
    chatwoot = FakeChatwoot()
    monkeypatch.setattr(main, "chatwoot", chatwoot)
    return chatwoot


@pytest.fixture
def client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test")


def ask(client, text="oi"):
    return client.post("/v1/messages", json={"guardian_id": "g01", "guardian_name": "Marina", "content": text})


def webhook(client, **payload):
    return client.post("/webhooks/chatwoot", json=payload)


def human_message(msg_id=500, content="Olá!", name="Pedro Silva"):
    return {"event": "message_created", "message_type": "outgoing", "id": msg_id, "content": content,
            "conversation": {"id": 10}, "sender": {"name": name}}


@pytest.mark.anyio
async def test_handoff_tells_parent_switches_to_human_and_categorizes(client, fake, monkeypatch):
    async def boom(*_a, **_k):
        raise Escalate("assunto sensível", "financeiro", "mensalidade")
    monkeypatch.setattr(main, "answer", boom)
    response = await ask(client, "mensalidade")
    assert response.json() == {"status": "handoff", "reply": HANDOFF_TEXT}
    assert conversation("g01")["mode"] == "human"
    assert fake.escalations == [("financeiro", "assunto sensível", "mensalidade")]
    assert [h["sender"] for h in history("g01")] == ["guardian", "agent"]


@pytest.mark.anyio
async def test_unexpected_llm_failure_still_hands_off_with_json(client, fake, monkeypatch):
    async def boom(*_a, **_k):
        raise RuntimeError("x")
    monkeypatch.setattr(main, "answer", boom)
    response = await ask(client)
    assert response.json()["status"] == "handoff"


@pytest.mark.anyio
async def test_bot_is_silent_while_human_then_resumes_after_resolve(client, fake, monkeypatch):
    calls = []

    async def fake_answer(*args, **kwargs):
        calls.append(args)
        if len(calls) == 1:
            raise Escalate("x", "outros", "q")
        return "resposta do bot"
    monkeypatch.setattr(main, "answer", fake_answer)
    await ask(client, "primeira")
    assert (await ask(client, "segunda")).json() == {"status": "queued"}
    assert len(calls) == 1
    await webhook(client, event="conversation_status_changed", status="resolved", id=10)  # real Chatwoot shape
    assert conversation("g01")["mode"] == "auto"
    assert (await ask(client, "terceira")).json()["reply"] == "resposta do bot"
    agent_messages = [h["content"] for h in history("g01") if h["sender"] == "agent"]
    assert agent_messages.count("resposta do bot") == 1


@pytest.mark.anyio
async def test_bot_echo_through_webhook_is_not_duplicated(client, fake, monkeypatch):
    async def fake_answer(*_a, **_k):
        return "resposta"
    monkeypatch.setattr(main, "answer", fake_answer)

    async def echo(msg_id, content, bot):  # webhook arrives before create_message could mark delivery
        payload = human_message(msg_id, content, "Admin")
        if bot:
            payload["content_attributes"] = {"apolo_bot": True}
        await webhook(client, **payload)
    fake.on_message = echo
    await ask(client)
    assert [h["sender"] for h in history("g01")] == ["guardian", "agent"]


@pytest.mark.anyio
async def test_human_reply_gets_name_prefix_and_is_idempotent(client, fake, monkeypatch):
    async def boom(*_a, **_k):
        raise Escalate()
    monkeypatch.setattr(main, "answer", boom)
    await ask(client)
    await webhook(client, **human_message())
    await webhook(client, **human_message())
    humans = [h["content"] for h in history("g01") if h["sender"] == "human"]
    assert humans == ["*Pedro Silva:* Olá!"]


@pytest.mark.anyio
async def test_private_notes_are_not_forwarded(client, fake, monkeypatch):
    async def boom(*_a, **_k):
        raise Escalate()
    monkeypatch.setattr(main, "answer", boom)
    await ask(client)
    await webhook(client, **{**human_message(), "private": True})
    assert not [h for h in history("g01") if h["sender"] == "human"]


@pytest.mark.anyio
async def test_reset_wipes_history_and_chatwoot_and_is_refused_through_a_tunnel(client, fake, monkeypatch):
    async def reply(*_a, **_k):
        return "Olá!"
    monkeypatch.setattr(main, "answer", reply)
    await ask(client, "oi")
    assert (await client.post("/v1/admin/reset", headers={"X-Forwarded-For": "1.2.3.4"})).status_code == 403
    assert history("g01")
    response = await client.post("/v1/admin/reset")
    assert response.json() == {"status": "cleared", "conversations": 1, "chatwoot_failures": []}
    assert history("g01") == [] and conversation("g01") is None
    assert fake.deleted == [(10, 1)]
