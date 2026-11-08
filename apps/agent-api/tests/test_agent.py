import pytest

from app import agent
from app.agent import Escalate, answer, guard


CONTEXT = {
    "school": {"id": "horizonte", "name": "Escola Horizonte"},
    "guardian": {"id": "g01", "name": "Responsável de Lucas"},
    "students": [{"name": "Lucas Almeida", "grades": [{"subject": "Matemática", "term": "1º bimestre", "score": 8.0}]}],
}
NAMES = ["Lucas Almeida", "Pedro Santos"]


def test_sensitive_questions_are_escalated():
    with pytest.raises(Escalate):
        guard("Meu filho precisa de remédio na escola?", CONTEXT)


def test_other_student_is_escalated():
    with pytest.raises(Escalate):
        guard("Como está Pedro Santos?", CONTEXT, NAMES)


def test_own_student_is_allowed():
    guard("Qual foi a nota de Lucas Almeida?", CONTEXT)


class Script:
    """Stands in for Ollama (replies popped in order) and for school-data's query endpoint."""
    def __init__(self, replies, query_result=None):
        self.replies, self.sent, self.queries = list(replies), [], []
        self.query_result = query_result or {"columns": ["score"], "rows": [[8.0]], "truncated": False}

    def install(self, monkeypatch):
        async def fake_context(_s, _g):
            return CONTEXT

        async def fake_names(_s):
            return NAMES

        async def fake_schema():
            return "grades(student_id, subject, term, score)"

        async def fake_query(_s, _g, sql):
            self.queries.append(sql)
            return self.query_result

        async def fake_chat(messages):
            self.sent.append(list(messages))
            return self.replies.pop(0)
        for name, fn in [("school_context", fake_context), ("school_student_names", fake_names), ("school_schema", fake_schema),
                         ("school_query", fake_query), ("_chat", fake_chat)]:
            monkeypatch.setattr(agent, name, fn)
        return self


@pytest.mark.anyio
async def test_answer_after_a_query_uses_the_query_result(monkeypatch):
    script = Script(['{"sql":"SELECT score FROM grades"}', '{"answer":"A nota é 8,0."}']).install(monkeypatch)
    assert await answer("horizonte", "g01", "Qual foi a nota de Lucas Almeida?") == "A nota é 8,0."
    assert script.queries == ["SELECT score FROM grades"]
    assert "RESULTADO" in script.sent[1][-1]["content"]  # the result is fed back to the model


@pytest.mark.anyio
async def test_greeting_needs_no_query(monkeypatch):
    script = Script(['{"answer":"Olá! Como posso ajudar?"}']).install(monkeypatch)
    assert await answer("horizonte", "g01", "oi") == "Olá! Como posso ajudar?"
    assert script.queries == []


@pytest.mark.anyio
async def test_number_not_in_any_query_result_escalates(monkeypatch):
    """The model cannot quote a figure it did not fetch (or invented): 9,5 was never returned."""
    Script(['{"sql":"SELECT score FROM grades"}', '{"answer":"A nota é 9,5."}']).install(monkeypatch)
    with pytest.raises(Escalate):
        await answer("horizonte", "g01", "Qual foi a nota de Lucas Almeida?")


@pytest.mark.anyio
async def test_number_without_any_query_escalates(monkeypatch):
    Script(['{"answer":"A nota é 8,0."}']).install(monkeypatch)  # right number, but guessed from nowhere
    with pytest.raises(Escalate):
        await answer("horizonte", "g01", "Qual foi a nota de Lucas Almeida?")


@pytest.mark.anyio
async def test_rejected_sql_is_reported_back_so_the_model_can_retry(monkeypatch):
    script = Script(['{"sql":"SELECT * FROM guardians"}', '{"sql":"SELECT score FROM grades"}', '{"answer":"A nota é 8,0."}'],
                    {"error": "access to guardians.id is prohibited"}).install(monkeypatch)
    results = iter([{"error": "access to guardians.id is prohibited"}, {"columns": ["score"], "rows": [[8.0]], "truncated": False}])

    async def fake_query(_s, _g, sql):
        script.queries.append(sql)
        return next(results)
    monkeypatch.setattr(agent, "school_query", fake_query)
    assert await answer("horizonte", "g01", "nota?") == "A nota é 8,0."
    assert "prohibited" in script.sent[1][-1]["content"]


@pytest.mark.anyio
async def test_endless_querying_escalates(monkeypatch):
    Script(['{"sql":"SELECT 1"}'] * agent.MAX_STEPS).install(monkeypatch)
    with pytest.raises(Escalate):
        await answer("horizonte", "g01", "nota?")


@pytest.mark.anyio
async def test_model_escalar_escalates(monkeypatch):
    Script(['{"answer":"ESCALAR"}', '{"answer":"ESCALAR"}']).install(monkeypatch)  # second one is the retry after the nudge
    with pytest.raises(Escalate):
        await answer("horizonte", "g01", "A escola oferece transporte?")


@pytest.mark.anyio
async def test_history_is_sent_to_the_model_in_order(monkeypatch):
    """Regression: 'por que?' was answered as if it were the first message."""
    script = Script(['{"answer":"Oi!"}']).install(monkeypatch)
    history = [{"sender": "guardian", "content": "qual a nota?"}, {"sender": "agent", "content": "É 8,0."},
               {"sender": "human", "content": "Confirmado."}]
    await answer("horizonte", "g01", "por que?", history)
    messages = script.sent[0]
    tail = messages[len(agent.FEW_SHOT) + 1:]
    assert [m["role"] for m in tail] == ["user", "assistant", "assistant", "user"]
    assert tail[-1]["content"] == "por que?"


def test_history_is_capped_and_ignores_unknown_senders():
    long = [{"sender": "guardian", "content": f"m{i}"} for i in range(30)] + [{"sender": "system", "content": "erro"}]
    assert len(agent.history_messages(long)) == agent.HISTORY_TURNS - 1  # trailing system item dropped after slicing
    assert agent.history_messages(None) == []


@pytest.mark.anyio
async def test_model_never_sees_student_data_only_identity_and_schema(monkeypatch):
    script = Script(['{"answer":"Oi!"}']).install(monkeypatch)
    await answer("horizonte", "g01", "oi")
    prompt = " ".join(m["content"] for m in script.sent[0])
    assert "Lucas Almeida" in prompt and "grades(student_id" in prompt
    assert "8.0" not in prompt and "score\": 8" not in prompt
    assert "Pedro" not in prompt


@pytest.mark.anyio
async def test_school_data_down_fails_closed(monkeypatch):
    async def broken(*_args):
        raise agent.httpx.ConnectError("down")
    monkeypatch.setattr(agent, "school_context", broken)
    with pytest.raises(Escalate):
        await answer("horizonte", "g01", "Qual a nota?")


@pytest.mark.anyio
async def test_number_from_the_question_is_not_a_grounded_fact(monkeypatch):
    """The guardian's own (wrong) figure must not be confirmable just because they typed it."""
    Script(['{"sql":"SELECT score FROM grades"}', '{"answer":"Sim, ele tirou 9."}']).install(monkeypatch)
    with pytest.raises(Escalate):
        await answer("horizonte", "g01", "ele tirou 9, né?")


@pytest.mark.anyio
async def test_period_ordinal_from_the_question_can_be_echoed(monkeypatch):
    """Regression (found with qwen2.5:14b): '...no 1º bimestre' was escalated because 1 was not in the result."""
    Script(['{"sql":"SELECT score FROM grades"}', '{"answer":"A nota no 1º bimestre foi 8,0."}']).install(monkeypatch)
    assert "1º bimestre" in await answer("horizonte", "g01", "Qual a nota no 1º bimestre?")
