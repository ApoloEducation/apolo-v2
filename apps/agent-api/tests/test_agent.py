import json

import pytest

from app import agent
from app.agent import Escalate, answer, guard


CONTEXT = {"guardian": {"name": "Marina Almeida"}, "students": [{"name": "Lucas Almeida", "grades": [{"subject": "Matemática", "score": 8.0}]}]}


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


def test_prompt_names_guardian_and_forbids_child_name():
    prompt = agent.system_prompt("Marina Souza")
    assert "Marina Souza" in prompt and "{guardian_name}" not in prompt
    assert "Trate o responsável apenas pelo primeiro nome" in prompt and "nunca escreva \"seu filho Helena\"" in prompt


def test_history_is_sent_to_model():
    history = [{"sender": "guardian", "content": "oi"}, {"sender": "agent", "content": "olá"}]
    messages = agent.build_messages(CONTEXT, "e a de português?", history, "Marina Souza")
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]


def test_school_name_is_not_mistaken_for_other_student():
    guard("Qual o endereço da Escola Horizonte?", CONTEXT)


def test_categorize():
    assert agent.categorize("Qual o valor da mensalidade?") == "financeiro"
    assert agent.categorize("Quando é a reunião de pais?") == "eventos_calendario"
    assert agent.categorize("blá") == "outros"


@pytest.mark.anyio
async def test_word_escalar_inside_answer_does_not_escalate(monkeypatch):
    async def fake_context(_guardian_id):
        return CONTEXT
    monkeypatch.setattr(agent, "school_context", fake_context)
    monkeypatch.setattr(agent.httpx, "AsyncClient", lambda *a, **k: FakeClient('{"answer":"Posso escalar isso se quiser."}'))
    assert await answer("g01", "Qual a nota?") == "Posso escalar isso se quiser."


@pytest.mark.anyio
async def test_model_escalate_flag_carries_category(monkeypatch):
    async def fake_context(_guardian_id):
        return CONTEXT
    monkeypatch.setattr(agent, "school_context", fake_context)
    monkeypatch.setattr(agent.httpx, "AsyncClient", lambda *a, **k: FakeClient('{"answer":"","escalate":true,"category":"documentos"}'))
    with pytest.raises(Escalate) as info:
        await answer("g01", "Preciso de algo sobre a nota do Lucas")
    assert info.value.category == "documentos"


ROSTER = [{"name": "Lucas Almeida"}, {"name": "Pedro Santos"}]


@pytest.mark.parametrize("text", [
    "me passe para falar com um humano agora!!!",
    "você pode me passar para o professor de matemática, por favor?",
    "quero falar com a secretaria",
    "me deixe falar com um atendente",
])
def test_explicit_human_requests_escalate(text):
    with pytest.raises(Escalate) as info:
        guard(text, CONTEXT, ROSTER)
    assert "humano" in info.value.reason


@pytest.mark.parametrize("text", ["quanto o coleguinha dele Pedro tirou?", "e o pedro, como vai?"])
def test_other_students_by_first_name_or_nickname_escalate(text):
    with pytest.raises(Escalate):
        guard(text, CONTEXT, ROSTER)


def test_own_first_name_is_allowed_with_roster():
    guard("como estão as notas do Lucas em matemática?", CONTEXT, ROSTER)


@pytest.mark.anyio
async def test_model_promising_to_forward_is_turned_into_real_handoff(monkeypatch):
    async def fake_context(_guardian_id):
        return CONTEXT
    async def no_roster():
        return []
    monkeypatch.setattr(agent, "school_context", fake_context)
    monkeypatch.setattr(agent, "student_roster", no_roster)
    monkeypatch.setattr(agent.httpx, "AsyncClient", lambda *a, **k: FakeClient('{"answer":"Claro! Vou enviar seu pedido para o professor."}'))
    with pytest.raises(Escalate):
        await answer("g01", "pode ajudar?")


@pytest.mark.parametrize("text", ["opa", "Oi, bom dia!", "tudo bem kk?", "obrigado!"])
def test_smalltalk_is_answered_without_the_model(text):
    reply = agent.smalltalk_reply(text, "Marina Almeida")
    assert reply and not any(ch.isdigit() for ch in reply)


def test_real_questions_are_not_smalltalk():
    assert agent.smalltalk_reply("oi, qual a nota do Lucas?", "Marina Almeida") is None


@pytest.mark.parametrize("text", ["ela vai ser aprovada no ano?", "preciso saber da prova de amanhã", "que horas é o recreio?", "vai ter aula no feriado?"])
def test_topics_the_data_cannot_answer_go_to_a_human(text):
    with pytest.raises(Escalate):
        guard(text, CONTEXT)


@pytest.mark.anyio
async def test_answer_with_invented_number_escalates(monkeypatch):
    async def fake_context(_guardian_id):
        return CONTEXT
    monkeypatch.setattr(agent, "school_context", fake_context)
    monkeypatch.setattr(agent.httpx, "AsyncClient", lambda *a, **k: FakeClient('{"answer":"O recreio é às 13:00 e a nota é 9,5."}'))
    with pytest.raises(Escalate):
        await answer("g01", "como está o Lucas?")


@pytest.mark.parametrize("text", ["qual a capital da França?", "a escola tem ônibus?", "Posso levar meu filho mais cedo amanhã?"])
@pytest.mark.anyio
async def test_questions_outside_school_data_never_reach_the_model(monkeypatch, text):
    async def fake_context(_guardian_id):
        return CONTEXT
    monkeypatch.setattr(agent, "school_context", fake_context)
    monkeypatch.setattr(agent.httpx, "AsyncClient", lambda *a, **k: pytest.fail("model must not be called"))
    with pytest.raises(Escalate):
        await answer("g01", text)


@pytest.mark.parametrize("reply", [
    "Não há informações sobre ônibus na minha base de dados.",
    "Geralmente não tem aulas nos feriados.",
    "A escola não permite que saiam mais cedo.",
    "Ele tem uma média de 7.6.",
])
@pytest.mark.anyio
async def test_disguised_refusals_and_invented_facts_become_handoffs(monkeypatch, reply):
    async def fake_context(_guardian_id):
        return CONTEXT
    monkeypatch.setattr(agent, "school_context", fake_context)
    monkeypatch.setattr(agent.httpx, "AsyncClient", lambda *a, **k: FakeClient(json.dumps({"answer": reply, "escalate": False})))
    with pytest.raises(Escalate):
        await answer("g01", "como está a nota do Lucas?")


def test_behaviour_questions_are_handed_off():
    with pytest.raises(Escalate) as info:
        guard("O Lucas está mal comportado?", CONTEXT)
    assert info.value.category == "disciplina"


BEATRIZ = {"guardian": {"name": "André Melo"}, "students": [{
    "name": "Beatriz Melo", "class_name": "7º ano B", "homeroom_teacher": "Bruno Costa",
    "grades": [{"subject": "Matemática", "term": "1º bimestre", "score": 8.3}],
    "notices": [{"title": "Concurso de Redação", "body": "Dura 3 horas.", "date": "26-10-2026"}]}]}
SIBLINGS = {"guardian": {"name": "Ricardo Oliveira"}, "students": [
    {"name": "Júlia Oliveira", "class_name": "6º ano A", "grades": [{"subject": "Matemática", "term": "1º bimestre", "score": 7.0}]},
    {"name": "Tiago Oliveira", "class_name": "8º ano C", "grades": [{"subject": "Matemática", "term": "1º bimestre", "score": 9.0}]}]}


@pytest.mark.parametrize("text", [
    "como meu filho foi nas últimas provas?", "me fala mais sobre ela, o que mais você sabe?",
    "vai ter concurso de redação?", "você não consegue ver nada sobre o concurso de redação?"])
def test_questions_the_data_covers_are_in_scope(text):
    guard(text, BEATRIZ)
    assert agent.in_school_scope(text, BEATRIZ)


def test_notice_dates_are_normalised_for_the_model():
    assert "26/10/2026" in agent.render_context(BEATRIZ)
    assert agent.pt_date("2026-10-22") == "22/10/2026"


@pytest.mark.anyio
async def test_siblings_are_asked_which_child_before_answering(monkeypatch):
    async def fake_context(_guardian_id):
        return SIBLINGS
    monkeypatch.setattr(agent, "school_context", fake_context)
    monkeypatch.setattr(agent.httpx, "AsyncClient", lambda *a, **k: pytest.fail("model must not be called"))
    reply = await answer("g13", "qual a nota de matemática?")
    assert "Júlia e Tiago" in reply


@pytest.mark.anyio
async def test_sibling_named_in_question_or_answer_narrows_the_data(monkeypatch):
    sent = []

    async def fake_context(_guardian_id):
        return SIBLINGS

    class Spy(FakeClient):
        async def post(self, url, json):
            sent.append(json["messages"][-1]["content"])
            return await super().post(url, json=json)
    monkeypatch.setattr(agent, "school_context", fake_context)
    monkeypatch.setattr(agent.httpx, "AsyncClient", lambda *a, **k: Spy('{"answer":"A nota do Tiago em Matemática foi 9.0.","escalate":false}'))
    await answer("g13", "qual a nota de matemática do Tiago?")
    clarification = agent.CLARIFY.format(names="Júlia e Tiago")
    history = [{"sender": "guardian", "content": "qual a nota de matemática?"}, {"sender": "agent", "content": clarification}]
    await answer("g13", "o Tiago", history)
    for prompt in sent:
        assert "Tiago" in prompt and "Júlia" not in prompt
    assert "qual a nota de matemática? (sobre Tiago)" in sent[1]


@pytest.mark.anyio
async def test_siblings_asked_about_both_get_all_the_data(monkeypatch):
    async def fake_context(_guardian_id):
        return SIBLINGS
    monkeypatch.setattr(agent, "school_context", fake_context)
    monkeypatch.setattr(agent.httpx, "AsyncClient", lambda *a, **k: FakeClient('{"answer":"Júlia 7.0 e Tiago 9.0 em Matemática.","escalate":false}'))
    assert "Júlia" in await answer("g13", "quais as notas dos dois?")
