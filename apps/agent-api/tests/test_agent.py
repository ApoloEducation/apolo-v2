import pytest

from app import agent
from app.agent import Escalate, answer, guard


CONTEXT = {"students": [{"name": "Lucas Almeida"}]}


def test_sensitive_questions_are_escalated():
    with pytest.raises(Escalate):
        guard("Meu filho precisa de remédio na escola?", CONTEXT)


def test_other_student_is_escalated():
    with pytest.raises(Escalate):
        guard("Como está Pedro Santos?", CONTEXT)


def test_own_student_is_allowed():
    guard("Qual foi a nota de Lucas Almeida?", CONTEXT)


class FakeResponse:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        return None

    def json(self):
        return {"message": {"content": self.content}}


class FakeClient:
    def __init__(self, content, *args, **kwargs):
        self.content = content

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def post(self, *args, **kwargs):
        return FakeResponse(self.content)


@pytest.mark.anyio
async def test_model_answer_is_returned(monkeypatch):
    async def fake_context(_guardian_id):
        return CONTEXT
    monkeypatch.setattr(agent, "school_context", fake_context)
    monkeypatch.setattr(agent.httpx, "AsyncClient", lambda *args, **kwargs: FakeClient('{"answer":"A nota é 8,0."}'))
    assert await answer("g01", "Qual foi a nota de Lucas Almeida?") == "A nota é 8,0."


@pytest.mark.anyio
async def test_model_without_answer_escalates(monkeypatch):
    async def fake_context(_guardian_id):
        return CONTEXT
    monkeypatch.setattr(agent, "school_context", fake_context)
    monkeypatch.setattr(agent.httpx, "AsyncClient", lambda *args, **kwargs: FakeClient('{"answer":"ESCALAR"}'))
    with pytest.raises(Escalate):
        await answer("g01", "A escola oferece transporte?")
