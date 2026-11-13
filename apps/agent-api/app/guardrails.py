"""Strict, fail-closed guardrails around the school assistant.

Layers: input length (here) and theme classification (classifier.py) before the model sees anything,
context (data minimisation) and output (before the answer reaches the guardian). Any violation raises Escalate.
"""
import json
import re
import unicodedata

MAX_QUESTION = 600
MAX_ANSWER = 900


class Escalate(Exception):
    """Hand the conversation to a human. `theme` says why (stored, and sent to Chatwoot as the `motivo-<theme>` label)."""

    def __init__(self, theme: str = "erro-agente", trace: dict | None = None):
        super().__init__(theme)
        self.theme = theme
        self.trace = trace or {}  # debugging/benchmark only (see agent.Answer.trace)


def normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()


# "I don't know" replies: the model was told to answer ESCALAR instead, so the guardian reaches a human rather than a dead end.
NO_INFO = re.compile(
    r"\bnao (tenho|temos|possuo|possuimos|ha|existe|existem|consta|constam)\b.{0,50}(informac|dado|acesso|registro)|"
    r"(informac\w*|dados?)\b.{0,50}\bnao (esta|estao|sao|foram|consta|constam|mencion\w*|fornecid\w*|dispon\w*)|"
    r"\binfelizmente\b|\bdesculpe, mas\b|\bnao (sei|consigo (informar|confirmar|responder))\b")
# Replies that talk about the tooling or stall instead of answering must never reach the guardian.
STALL = re.compile(r"\b(aguarde|um momento|vou (consultar|verificar|procurar|buscar)|banco de dados|consult[ae] (o )?banco|sql|resultado da consulta|erro na consulta|escalonar|escalar para)\b")
# "A secretaria pode orientar": sends the guardian away without notifying anyone. The handoff does that properly.
REFERRAL = re.compile(r"\b(secretaria|atendente|coordenacao|direcao)\b.{0,40}\b(pode|podera|podem|vai|consegue)\b.{0,25}\b(orientar|ajudar|informar|esclarecer|confirmar|resolver)|"
                      r"\b(procure|procurar|contate|entre em contato|fale|falar|confirme|confirmar) (com |diretamente com )?(a |o )?(secretaria|atendente|coordenacao|direcao)")
URL = re.compile(r"(https?://|www\.|\b\w+\.(com|br|org|net|io)\b)")
CODE_LIKE = re.compile(r"(```|[{}\[\]]|<[a-z/][^>]*>|\bSELECT\b|\bimport\b|\bdef\b)", re.I)
NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
ORDINAL = re.compile(r"\b\d{1,2}\s?[º°ª]")


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", normalize(text)))


def check_input(question: str) -> None:
    """Only the plain-code limit lives here; themes (sensitive, injection, other students...) are decided by classifier.py."""
    if len(question) > MAX_QUESTION:
        raise Escalate("mensagem-longa")


def minimal_context(context: dict) -> dict:
    """Identity only. Facts are fetched by the model through scoped SQL; internal ids never leave the service."""
    students = [{"name": s["name"], "class_name": s.get("class_name"), "shift": s.get("shift"), "homeroom_teacher": s.get("homeroom_teacher")}
                for s in context["students"]]
    return {"school": context.get("school", {}).get("name"), "guardian": context.get("guardian", {}).get("name"), "students": students}


def _numbers(text: str) -> set[float]:
    return {float(token.replace(",", ".")) for token in NUMBER.findall(text)}


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _derived_numbers(results: list) -> set[float]:
    """Simple arithmetic over query results that the code can verify itself, so a correct "1 falta" or "100%"
    passes even when the model did the sum instead of the SQL: row counts, and per row the difference and the
    percentage of any two values; per column the mean and the differences between rows."""
    derived: set[float] = set()
    for rows in results:
        derived.add(float(len(rows)))
        for row in rows:
            values = [float(v) for v in row if _is_number(v)]
            for i, a in enumerate(values):
                for j, b in enumerate(values):
                    if i != j:
                        derived.add(round(abs(a - b), 2))
                        if b:
                            derived |= {float(round(a / b * 100)), round(a / b * 100, 1), round(a / b * 100, 2)}
        if 1 < len(rows) <= 20:
            for column in zip(*rows):
                values = [float(v) for v in column if _is_number(v)]
                if len(values) > 1:
                    mean = sum(values) / len(values)
                    derived |= {round(mean, 1), round(mean, 2)}
                    derived |= {round(abs(a - b), 2) for i, a in enumerate(values) for b in values[i + 1:]}
    return derived


def check_output(text: str, context: dict, all_names: list[str]) -> str:
    text = (text or "").strip()
    if not text or len(text) > MAX_ANSWER:
        raise Escalate("tamanho")
    if "ESCALAR" in text.upper():
        raise Escalate("escalar")
    if URL.search(text) or CODE_LIKE.search(text):
        raise Escalate("link-ou-codigo")
    norm, words = normalize(text), _words(text)
    if REFERRAL.search(norm):
        raise Escalate("encaminhamento")
    if STALL.search(norm):
        raise Escalate("enrolacao")
    own = {normalize(s["name"]) for s in context["students"]}
    own_parts = {part for n in own for part in n.split()}
    for name in all_names:
        full = normalize(name)
        if full in own:
            continue
        first = full.split()[0]
        if full in norm or (first in words and first not in own_parts):
            raise Escalate("nome-de-outro-aluno")
    # Numeric grounding: every figure in the answer must come from the authorised data.
    typed = set(context.get("numeros_da_pergunta", []))
    facts = {key: value for key, value in context.items() if key != "numeros_da_pergunta"}
    allowed = _numbers(json.dumps(facts, ensure_ascii=False)) | _derived_numbers(context.get("resultados", []))
    allowed |= {float(round(n)) for n in allowed}  # "98%" for 97,6 is fine
    used = _numbers(ORDINAL.sub(" ", text))  # "2º bimestre", "6º ano" name a term or a class; they are not figures
    # "I don't know" is a dead end unless the reply also carries verified data ("6,8 é a menor nota; o motivo não consta").
    if NO_INFO.search(norm) and not (used & allowed) - typed:
        raise Escalate("sem-informacao")
    # Figures the guardian typed are not facts ("ele tirou 9, né?" must not be confirmed), but may be repeated next
    # to a verified one ("não foi 9, foi 7,1").
    if (used & allowed) - typed:
        used -= typed
    if not used <= allowed:
        raise Escalate("numero-nao-verificado")
    return text
