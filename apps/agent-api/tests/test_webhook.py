import pytest
from fastapi.testclient import TestClient

from app import state


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "PATH", tmp_path / "agent.sqlite3")
    from app.main import app
    with TestClient(app) as test_client:
        yield test_client


def test_resolved_status_event_returns_conversation_to_auto(client):
    state.save_conversation("aurora", "g01", 4, 9, "human")
    # Real Chatwoot payload shape: the conversation is the payload, so `id` is top-level.
    client.post("/webhooks/chatwoot", json={"event": "conversation_status_changed", "id": 4, "status": "resolved"})
    assert state.conversation("aurora", "g01")["mode"] == "auto"


def test_other_status_changes_keep_human_mode(client):
    state.save_conversation("aurora", "g01", 4, 9, "human")
    client.post("/webhooks/chatwoot", json={"event": "conversation_status_changed", "id": 4, "status": "open"})
    assert state.conversation("aurora", "g01")["mode"] == "human"


def test_human_message_is_stored_under_its_school(client):
    state.save_conversation("aurora", "g01", 4, 9, "human")
    client.post("/webhooks/chatwoot", json={"event": "message_created", "message_type": "outgoing", "private": False,
                                            "id": 77, "content": "Olá!", "conversation": {"id": 4}})
    assert state.history("aurora", "g01")[0]["sender"] == "human"
    assert state.history("horizonte", "g01") == []


def _fake_chatwoot(monkeypatch, client, webhook_first: bool):
    """Chatwoot stub; when `webhook_first`, its message_created webhook fires before our POST returns."""
    from app import main
    monkeypatch.setattr(main, "school_data", lambda path: _async([{"id": "g06"}]))

    async def ensure(*_args):
        return 9, 4

    async def message(conversation_id, content, message_type="incoming"):
        if message_type == "outgoing" and webhook_first:
            await main.chatwoot_webhook({"event": "message_created", "message_type": "outgoing", "private": False,
                                         "id": 501, "content": content, "conversation": {"id": conversation_id}})
        return {"id": 501}

    async def fake_answer(school, guardian, question, history=None):
        return "A nota de Matemática é 7,1."
    monkeypatch.setattr(main.chatwoot, "ensure_contact_and_conversation", ensure)
    monkeypatch.setattr(main.chatwoot, "message", message)
    monkeypatch.setattr(main, "answer", fake_answer)


async def _async(value):
    return value


def test_agent_reply_is_not_duplicated_when_webhook_beats_the_post_response(client, monkeypatch):
    """Regression: the guardian saw the agent's reply twice, the second labelled 'Atendente'."""
    _fake_chatwoot(monkeypatch, client, webhook_first=True)
    response = client.post("/v1/messages", json={"school_id": "aurora", "guardian_id": "g06", "guardian_name": "G", "content": "nota?"})
    assert response.status_code == 200
    assert [m["sender"] for m in state.history("aurora", "g06")] == ["guardian", "agent"]


def test_agent_reply_is_not_duplicated_when_webhook_arrives_after(client, monkeypatch):
    _fake_chatwoot(monkeypatch, client, webhook_first=False)
    client.post("/v1/messages", json={"school_id": "aurora", "guardian_id": "g06", "guardian_name": "G", "content": "nota?"})
    client.post("/webhooks/chatwoot", json={"event": "message_created", "message_type": "outgoing", "private": False,
                                            "id": 501, "content": "A nota de Matemática é 7,1.", "conversation": {"id": 4}})
    assert [m["sender"] for m in state.history("aurora", "g06")] == ["guardian", "agent"]


def test_real_human_reply_with_same_text_as_a_later_message_is_still_delivered(client):
    state.save_conversation("aurora", "g06", 4, 9, "human")
    for message_id in (11, 12):
        client.post("/webhooks/chatwoot", json={"event": "message_created", "message_type": "outgoing", "private": False,
                                                "id": message_id, "content": "Ok", "conversation": {"id": 4}})
    assert [m["sender"] for m in state.history("aurora", "g06")] == ["human", "human"]


def test_webhook_retry_of_a_human_message_is_idempotent(client):
    state.save_conversation("aurora", "g06", 4, 9, "human")
    payload = {"event": "message_created", "message_type": "outgoing", "private": False, "id": 31, "content": "Oi", "conversation": {"id": 4}}
    client.post("/webhooks/chatwoot", json=payload)
    client.post("/webhooks/chatwoot", json=payload)
    assert len(state.history("aurora", "g06")) == 1


def test_previous_messages_are_passed_to_the_agent(client, monkeypatch):
    seen = {}
    _fake_chatwoot(monkeypatch, client, webhook_first=False)
    from app import main

    async def spy(school, guardian, question, history=None):
        seen["history"] = history
        return "Porque Ciências é a menor nota."
    monkeypatch.setattr(main, "answer", spy)
    state.save_message("aurora", "g06", "guardian", "qual matéria está pior?")
    state.save_message("aurora", "g06", "agent", "Ciências.")
    client.post("/v1/messages", json={"school_id": "aurora", "guardian_id": "g06", "guardian_name": "G", "content": "por quê?"})
    assert [(m["sender"], m["content"]) for m in seen["history"]] == [("guardian", "qual matéria está pior?"), ("agent", "Ciências.")]
