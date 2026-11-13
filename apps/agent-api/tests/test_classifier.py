import httpx
import pytest

from app import classifier
from app.classifier import ANSWER, DECLINE, ESCALATE, THEMES, build_question, build_state, classify, decide
from app.guardrails import Escalate


def test_every_theme_has_a_known_action_and_fits_the_model_limit():
    assert {action for action, _ in THEMES.values()} == {ESCALATE, DECLINE, ANSWER}
    assert 2 <= len(THEMES) <= classifier.MAX_THEMES


def test_top_answerable_theme_wins_when_nothing_sensitive_is_likely():
    result = decide({"notas": 0.9, "saude": 0.05, "frequencia": 0.05})
    assert (result.theme, result.action) == ("notas", ANSWER)


def test_sensitive_theme_wins_even_when_it_is_not_the_top_answer():
    result = decide({"notas": 0.6, "saude": 0.35, "frequencia": 0.05})
    assert (result.theme, result.action) == ("saude", ESCALATE)


def test_probabilities_of_escalating_themes_add_up():
    result = decide({"notas": 0.7, "saude": 0.16, "financeiro": 0.14})
    assert (result.theme, result.action) == ("saude", ESCALATE)


def test_threshold_is_configurable(monkeypatch):
    monkeypatch.setenv("CLASSIFIER_BLOCK_THRESHOLD", "0.5")
    assert decide({"notas": 0.6, "saude": 0.4}).action == ANSWER


@pytest.mark.parametrize("theme", ["injecao", "fora-do-escopo"])
def test_unsafe_and_off_topic_are_declined_not_escalated(theme):
    assert decide({theme: 0.8, "notas": 0.2}).action == DECLINE


def test_courtesy_is_answered():
    assert decide({"cortesia": 0.95, "fora-do-escopo": 0.05}).action == ANSWER


def test_missing_probabilities_fall_back_to_the_choice():
    assert decide({}, top="saude").theme == "saude"


def test_unusable_response_escalates():
    with pytest.raises(Escalate) as raised:
        decide({"tema-inventado": 1.0}, top="tema-inventado")
    assert raised.value.theme == "erro-classificador"


def test_state_has_message_children_and_recent_turns_only():
    history = [{"sender": "guardian", "content": f"m{i}"} for i in range(6)]
    state = build_state("e em ciências?", ["Lucas Almeida"], history)
    assert state["mensagem"] == "e em ciências?" and state["filhos_do_responsavel"] == ["Lucas Almeida"]
    assert [turn["texto"] for turn in state["conversa_recente"]] == ["m3", "m4", "m5"]


def test_question_lists_every_theme_as_a_choice():
    question = build_question()["theme"]
    assert question["type"] == "choice" and set(question["criteria"]) == set(THEMES)


class FakeClient:
    sent = {}
    reply = {"answers": {"theme": {"type": "choice", "choice": "notas", "probabilities": {"notas": 0.97, "saude": 0.03}, "confidence": 0.9}}}
    fail = False

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def post(self, url, json=None, headers=None):
        FakeClient.sent = {"url": url, "json": json, "headers": headers}
        if FakeClient.fail:
            raise httpx.ConnectError("down")
        return type("R", (), {"status_code": 200, "raise_for_status": lambda s: None, "json": lambda s: FakeClient.reply})()


@pytest.fixture
def fake_http(monkeypatch):
    monkeypatch.setattr(classifier.httpx, "AsyncClient", FakeClient)
    FakeClient.fail, FakeClient.sent = False, {}
    FakeClient.reply = {"answers": {"theme": {"type": "choice", "choice": "notas", "probabilities": {"notas": 0.97, "saude": 0.03}, "confidence": 0.9}}}


@pytest.mark.anyio
async def test_ollama_request_shape(fake_http, monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setenv("OLLAMA_URL", "http://ollama:11434")
    result = await classify("qual a nota?", ["Lucas Almeida"])
    assert result.theme == "notas"
    assert FakeClient.sent["url"] == "http://ollama:11434/v1/systemone"
    assert FakeClient.sent["json"]["model"] == "d3:2b" and not FakeClient.sent["headers"]
    assert "theme" in FakeClient.sent["json"]["questions"]


@pytest.mark.anyio
async def test_openrouter_request_shape(fake_http, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPEN_ROUTER_API_KEY", "sk-test")
    await classify("qual a nota?", ["Lucas Almeida"])
    assert FakeClient.sent["url"] == "https://openrouter.ai/api/alpha/decisions"
    assert FakeClient.sent["json"]["model"] == "typesafe/jev-1.13"
    assert FakeClient.sent["headers"] == {"Authorization": "Bearer sk-test"}


@pytest.mark.anyio
async def test_transport_error_fails_closed(fake_http):
    FakeClient.fail = True
    with pytest.raises(Escalate) as raised:
        await classify("oi", [])
    assert raised.value.theme == "erro-classificador"


@pytest.mark.anyio
async def test_malformed_response_fails_closed(fake_http):
    FakeClient.reply = {"answers": {}}
    with pytest.raises(Escalate) as raised:
        await classify("oi", [])
    assert raised.value.theme == "erro-classificador"
