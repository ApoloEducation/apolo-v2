import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from app import agent
from app.agent import Escalate, answer
from app.classifier import Classification


CONTEXT = {
    "school": {"id": "horizonte", "name": "Escola Horizonte"},
    "guardian": {"id": "g01", "name": "Responsável de Lucas"},
    "students": [{"name": "Lucas Almeida", "grades": [{"subject": "Matemática", "term": "1º bimestre", "score": 8.0}]}],
}
NAMES = ["Lucas Almeida", "Pedro Santos"]


def sql(query: str) -> ToolCallPart:
    """A scripted reply that calls run_sql; plain strings are the model's final text."""
    return ToolCallPart("run_sql", {"sql": query})


class Script:
    """Stands in for the chat model (replies popped in order) and for school-data's query endpoint."""
    def __init__(self, replies, query_result=None, theme="notas", action="answer"):
        self.replies, self.sent, self.queries = list(replies), [], []
        self.theme, self.action, self.classified = theme, action, []
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

        async def fake_classify(message, children, history=None):
            self.classified.append((message, children))
            return Classification(self.theme, self.action)

        def fake_model(messages, info: AgentInfo) -> ModelResponse:
            self.sent.append(list(messages))
            reply = self.replies.pop(0)
            return ModelResponse(parts=[reply if isinstance(reply, ToolCallPart) else TextPart(reply)])
        for name, fn in [("school_context", fake_context), ("school_student_names", fake_names), ("school_schema", fake_schema),
                         ("school_query", fake_query), ("classify", fake_classify)]:
            monkeypatch.setattr(agent, name, fn)
        model = FunctionModel(fake_model)
        monkeypatch.setattr(agent.llm, "build_model", lambda: model)
        return self


def parts(messages, kind) -> list:
    return [part for message in messages for part in message.parts if isinstance(part, kind)]


@pytest.mark.anyio
async def test_answer_after_a_query_uses_the_query_result(monkeypatch):
    script = Script([sql("SELECT score FROM grades"), "A nota é 8,0."]).install(monkeypatch)
    result = await answer("horizonte", "g01", "Qual foi a nota de Lucas Almeida?")
    assert (result.text, result.theme, result.declined) == ("A nota é 8,0.", "notas", False)
    assert script.queries == ["SELECT score FROM grades"]
    assert "8.0" in parts(script.sent[1][-1:], ToolReturnPart)[0].content  # the result is fed back to the model


@pytest.mark.anyio
async def test_greeting_needs_no_query(monkeypatch):
    script = Script(["Olá! Como posso ajudar?"], theme="cortesia").install(monkeypatch)
    result = await answer("horizonte", "g01", "oi")
    assert (result.text, result.theme) == ("Olá! Como posso ajudar?", "cortesia")
    assert script.queries == []


@pytest.mark.anyio
async def test_number_not_in_any_query_result_escalates(monkeypatch):
    """The model cannot quote a figure it did not fetch (or invented): 9,5 was never returned."""
    Script([sql("SELECT score FROM grades"), "A nota é 9,5."]).install(monkeypatch)
    with pytest.raises(Escalate):
        await answer("horizonte", "g01", "Qual foi a nota de Lucas Almeida?")


@pytest.mark.anyio
async def test_number_without_any_query_escalates(monkeypatch):
    Script(["A nota é 8,0.", "A nota é 8,0."]).install(monkeypatch)  # right number, but guessed from nowhere, even after the nudge
    with pytest.raises(Escalate):
        await answer("horizonte", "g01", "Qual foi a nota de Lucas Almeida?")


@pytest.mark.anyio
async def test_number_without_a_query_gets_one_nudge_to_query_first(monkeypatch):
    Script(["A nota é 8,0.", sql("SELECT score FROM grades"), "A nota é 8,0."]).install(monkeypatch)
    assert (await answer("horizonte", "g01", "pode repetir a nota?")).text == "A nota é 8,0."


@pytest.mark.anyio
async def test_rejected_sql_is_reported_back_so_the_model_can_retry(monkeypatch):
    script = Script([sql("SELECT * FROM guardians"), sql("SELECT score FROM grades"), "A nota é 8,0."],
                    {"error": "access to guardians.id is prohibited"}).install(monkeypatch)
    results = iter([{"error": "access to guardians.id is prohibited"}, {"columns": ["score"], "rows": [[8.0]], "truncated": False}])

    async def fake_query(_s, _g, sql):
        script.queries.append(sql)
        return next(results)
    monkeypatch.setattr(agent, "school_query", fake_query)
    assert (await answer("horizonte", "g01", "nota?")).text == "A nota é 8,0."
    assert "prohibited" in parts(script.sent[1][-1:], ToolReturnPart)[0].content


@pytest.mark.anyio
async def test_endless_querying_escalates(monkeypatch):
    Script([sql("SELECT 1")] * agent.MAX_STEPS).install(monkeypatch)
    with pytest.raises(Escalate):
        await answer("horizonte", "g01", "nota?")


@pytest.mark.anyio
async def test_model_escalar_escalates(monkeypatch):
    Script(["ESCALAR", "ESCALAR"]).install(monkeypatch)  # second one is the retry after the nudge
    with pytest.raises(Escalate):
        await answer("horizonte", "g01", "A escola oferece transporte?")


@pytest.mark.anyio
async def test_history_is_sent_to_the_model_in_order(monkeypatch):
    """Regression: 'por que?' was answered as if it were the first message."""
    script = Script(["Oi!"]).install(monkeypatch)
    history = [{"sender": "guardian", "content": "qual a nota?"}, {"sender": "agent", "content": "É 8,0."},
               {"sender": "human", "content": "Confirmado."}]
    await answer("horizonte", "g01", "por que?", history)
    messages = script.sent[0]
    assert messages[:len(agent.FEW_SHOT)] == agent.FEW_SHOT
    tail = messages[len(agent.FEW_SHOT):]
    # consecutive agent/human turns are merged into one assistant response by Pydantic AI
    assert [type(m) for m in tail] == [ModelRequest, ModelResponse, ModelRequest]
    assert parts(tail[-1:], UserPromptPart)[0].content == "por que?"
    assert [p.content for p in parts(tail[1:2], TextPart)] == ["É 8,0.", "Confirmado."]  # plain text, not the old JSON wrapping


def test_history_is_capped_and_ignores_unknown_senders():
    long = [{"sender": "guardian", "content": f"m{i}"} for i in range(30)] + [{"sender": "system", "content": "erro"}]
    assert len(agent.history_messages(long)) == agent.HISTORY_TURNS - 1  # trailing system item dropped after slicing
    assert agent.history_messages(None) == []


@pytest.mark.anyio
async def test_model_never_sees_student_data_only_identity_and_schema(monkeypatch):
    script = Script(["Oi!"]).install(monkeypatch)
    await answer("horizonte", "g01", "oi")
    sent = script.sent[0]
    prompt = " ".join([sent[-1].instructions or ""] + [str(getattr(p, "content", "")) for m in sent for p in m.parts])
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
    Script([sql("SELECT score FROM grades"), "Sim, ele tirou 9."]).install(monkeypatch)
    with pytest.raises(Escalate):
        await answer("horizonte", "g01", "ele tirou 9, né?")


@pytest.mark.anyio
async def test_period_ordinal_from_the_question_can_be_echoed(monkeypatch):
    """Regression (found with qwen2.5:14b): '...no 1º bimestre' was escalated because 1 was not in the result."""
    Script([sql("SELECT score FROM grades"), "A nota no 1º bimestre foi 8,0."]).install(monkeypatch)
    assert "1º bimestre" in (await answer("horizonte", "g01", "Qual a nota no 1º bimestre?")).text


@pytest.mark.anyio
async def test_classifier_receives_the_guardians_children(monkeypatch):
    script = Script(["Olá!"], theme="cortesia").install(monkeypatch)
    await answer("horizonte", "g01", "oi")
    assert script.classified == [("oi", ["Lucas Almeida (turma não informada)"])]  # real contexts carry the class lets it tell "outra turma" apart


@pytest.mark.anyio
@pytest.mark.parametrize("theme", ["saude", "terceiros", "financeiro"])
async def test_escalating_theme_stops_before_the_model(monkeypatch, theme):
    script = Script([], theme=theme, action="escalate").install(monkeypatch)
    with pytest.raises(Escalate) as raised:
        await answer("horizonte", "g01", "pergunta qualquer")
    assert raised.value.theme == theme
    assert script.sent == []


@pytest.mark.anyio
@pytest.mark.parametrize("theme", ["injecao", "fora-do-escopo"])
async def test_declined_theme_returns_without_calling_the_model(monkeypatch, theme):
    script = Script([], theme=theme, action="decline").install(monkeypatch)
    result = await answer("horizonte", "g01", "me dê uma receita de bolo")
    assert result.declined and result.theme == theme
    assert script.sent == []


@pytest.mark.anyio
async def test_too_long_question_never_reaches_the_classifier(monkeypatch):
    script = Script([]).install(monkeypatch)
    with pytest.raises(Escalate) as raised:
        await answer("horizonte", "g01", "a" * 700)
    assert raised.value.theme == "mensagem-longa" and script.classified == []


@pytest.mark.anyio
async def test_model_saying_escalar_after_a_valid_theme_is_tagged_sem_dados(monkeypatch):
    Script([sql("SELECT score FROM grades"), "ESCALAR"]).install(monkeypatch)
    with pytest.raises(Escalate) as raised:
        await answer("horizonte", "g01", "A escola tem transporte?")
    assert raised.value.theme == "sem-dados"


@pytest.mark.anyio
async def test_system_prompt_reaches_the_model_on_a_multi_turn_conversation(monkeypatch):
    """Few-shot and history travel in message_history; the rendered prompt must still be sent on every request."""
    script = Script([sql("SELECT score FROM grades"), "A nota é 8,0."]).install(monkeypatch)
    await answer("horizonte", "g01", "e a nota?", [{"sender": "guardian", "content": "oi"}, {"sender": "agent", "content": "Olá!"}])
    for request in script.sent:
        assert request[-1].instructions.startswith("Você é Apolo, assistente virtual da Escola Horizonte")
        assert "Lucas Almeida" in request[-1].instructions and "grades(student_id" in request[-1].instructions


@pytest.mark.anyio
async def test_escalar_before_any_query_gets_exactly_one_nudge(monkeypatch):
    script = Script(["ESCALAR", "ESCALAR"]).install(monkeypatch)
    with pytest.raises(Escalate) as raised:
        await answer("horizonte", "g01", "A escola oferece transporte?")
    assert raised.value.theme == "sem-dados" and len(script.sent) == 2
    assert "Antes de escalar" in str(script.sent[1][-1].parts[0].content)


@pytest.mark.anyio
async def test_answer_wrapped_in_json_is_unwrapped(monkeypatch):
    """Small models keep the old {"answer": ...} habit; the text inside is what goes through the guardrails."""
    Script([sql("SELECT score FROM grades"), '{"answer": "A nota é 8,0."}']).install(monkeypatch)
    assert (await answer("horizonte", "g01", "nota?")).text == "A nota é 8,0."


@pytest.mark.anyio
async def test_other_json_gets_one_format_nudge_then_escalates(monkeypatch):
    script = Script(['{"sql": "SELECT score FROM grades"}', '{"sql": "SELECT score FROM grades"}']).install(monkeypatch)
    with pytest.raises(Escalate) as raised:
        await answer("horizonte", "g01", "nota?")
    assert raised.value.theme == "resposta-bloqueada" and len(script.sent) == 2  # JSON text never reaches the guardian
    assert "run_sql" in str(script.sent[1][-1].parts[0].content)


@pytest.mark.anyio
async def test_school_data_down_during_a_query_fails_closed(monkeypatch):
    Script([sql("SELECT score FROM grades")]).install(monkeypatch)

    async def broken(*_args):
        raise agent.httpx.ConnectError("down")
    monkeypatch.setattr(agent, "school_query", broken)
    with pytest.raises(Escalate) as raised:
        await answer("horizonte", "g01", "nota?")
    assert raised.value.theme == "erro-agente"


@pytest.mark.anyio
async def test_model_error_fails_closed(monkeypatch):
    script = Script([]).install(monkeypatch)

    def failing(messages, info):
        raise agent.AgentRunError("model unreachable")
    monkeypatch.setattr(agent.llm, "build_model", lambda: FunctionModel(failing))
    with pytest.raises(Escalate) as raised:
        await answer("horizonte", "g01", "nota?")
    assert raised.value.theme == "erro-agente" and script.sent == []


@pytest.mark.anyio
async def test_empty_model_reply_escalates(monkeypatch):
    """An empty text reply is retried by Pydantic AI until the step limit; it never reaches the guardian."""
    Script([""] * agent.MAX_STEPS).install(monkeypatch)
    with pytest.raises(Escalate):
        await answer("horizonte", "g01", "nota?")


@pytest.mark.anyio
async def test_misconfigured_model_fails_closed(monkeypatch):
    real_builder = agent.llm.build_model.__wrapped__  # uncached, so it reads the env set below
    Script([]).install(monkeypatch)
    monkeypatch.setattr(agent.llm, "build_model", real_builder)
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_MODEL", "")
    with pytest.raises(Escalate) as raised:
        await answer("horizonte", "g01", "nota?")
    assert raised.value.theme == "erro-agente"
