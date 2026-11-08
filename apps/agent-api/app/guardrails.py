"""Strict, fail-closed guardrails around the school assistant.

Three layers: input (before the model sees anything), context (data minimisation)
and output (before the answer reaches the guardian). Any violation raises Escalate.
"""
import json
import re
import unicodedata

MAX_QUESTION = 600
MAX_ANSWER = 900


class Escalate(Exception):
    pass


def normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()


# Patterns run on normalised text (lowercase, no accents).
SENSITIVE = re.compile(
    r"(saude|medic|remedio|laudo|diagnost|alergi|doenca|psicolog|terapia|bullying|disciplinar|indisciplin|suspens|"
    r"advertencia|violen|agress|juridic|advogad|process|financ|mensalidade|pagamento|boleto|desconto|"
    r"bolsa|cpf|rg\b|endereco|telefone|salario|demiss)"
)
INJECTION = re.compile(
    r"(ignore|ignora|esquec[ae]|desconsidere|desobede[cç]a).{0,40}(instruc|regra|prompt|anterior|acima)|"
    r"(system|developer|sistema)\s*prompt|prompt\s+(do|de)\s+sistema|"
    r"voce\s+(agora\s+)?(e|eh|sera|vai\s+ser)\b|finja|aja\s+como|atue\s+como|roleplay|jailbreak|"
    r"modo\s+(desenvolvedor|dev|deus)|\bdan\b|"
    r"(mostre|revele|exiba|liste|imprima|repita).{0,30}(instruc|regras|prompt|dados\s+(brutos|completos)|banco|sql|json)|"
    r"\bselect\b.{0,40}\bfrom\b|\bdrop\s+table\b|<\s*script|```|base64"
)
OTHER_PEOPLE = re.compile(r"\b(outro|outra|outros|outras)\s+(aluno|aluna|alunos|alunas|crianca|filho|filha)|\bcolegas?\b|\bturma\s+inteira\b|\btodos\s+os\s+alunos\b|\branking\b|\bmedia\s+da\s+turma\b|\b(maior|menor|melhor|pior)\s+(nota|aluno|aluna)\b|\bda\s+turma\b.{0,20}\b(maior|menor|melhor|pior|media|ranking)\b|\b(maior|menor|melhor|pior|media)\b.{0,20}\bda\s+turma\b")
CROSS_SCHOOL = re.compile(r"\boutra\s+escola\b|\bescola\s+(aurora|horizonte|vale\s*verde)\b|\bcolegio\s+aurora\b")
# "I don't know" replies: the model was told to answer ESCALAR instead, so the guardian reaches a human rather than a dead end.
NO_INFO = re.compile(
    r"\bnao (tenho|temos|possuo|possuimos|ha|existe|existem|consta|constam)\b.{0,50}(informac|dado|acesso|registro)|"
    r"(informac\w*|dados?)\b.{0,50}\bnao (esta|estao|sao|foram|consta|constam|mencion\w*|fornecid\w*|dispon\w*)|"
    r"\binfelizmente\b|\bdesculpe, mas\b|\bnao (sei|consigo (informar|confirmar|responder))\b")
# Replies that talk about the tooling or stall instead of answering must never reach the guardian.
STALL = re.compile(r"\b(aguarde|um momento|vou (consultar|verificar|procurar|buscar)|banco de dados|consult[ae] (o )?banco|sql|resultado da consulta|erro na consulta|escalonar|escalar para)\b")
NAMED_STUDENT = re.compile(r"\b[Aa]lun[oa]s?\s+([A-ZÁÉÍÓÚÂÊÔÃÕÇ][\wÀ-ÿ]+)")  # "o aluno Pedro", "a aluna Bianca"
URL = re.compile(r"(https?://|www\.|\b\w+\.(com|br|org|net|io)\b)")
CODE_LIKE = re.compile(r"(```|[{}\[\]]|<[a-z/][^>]*>|\bSELECT\b|\bimport\b|\bdef\b)", re.I)
NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", normalize(text)))


def check_input(question: str, own_names: list[str], all_names: list[str]) -> None:
    if len(question) > MAX_QUESTION:
        raise Escalate()
    text = normalize(question)
    if SENSITIVE.search(text) or INJECTION.search(text) or OTHER_PEOPLE.search(text) or CROSS_SCHOOL.search(text):
        raise Escalate()
    words = _words(question)
    own = {normalize(n) for n in own_names}
    own_parts = {part for n in own for part in n.split()}
    # A named student who is not the guardian's own child (even if the name is unknown to this school) must not be answered.
    for mentioned in NAMED_STUDENT.findall(question):
        if normalize(mentioned) not in own_parts:
            raise Escalate()
    for name in all_names:
        norm = normalize(name)
        if norm in own:
            continue
        if norm in text:
            raise Escalate()
        # First name alone is enough to flag, unless it is also the first name of an authorised student.
        first = norm.split()[0]
        if first in words and first not in own_parts:
            raise Escalate()


def minimal_context(context: dict) -> dict:
    """Identity only. Facts are fetched by the model through scoped SQL; internal ids never leave the service."""
    students = [{"name": s["name"], "class_name": s.get("class_name"), "shift": s.get("shift"), "homeroom_teacher": s.get("homeroom_teacher")}
                for s in context["students"]]
    return {"school": context.get("school", {}).get("name"), "guardian": context.get("guardian", {}).get("name"), "students": students}


def _numbers(text: str) -> set[float]:
    return {float(token.replace(",", ".")) for token in NUMBER.findall(text)}


def _derived_numbers(context: dict) -> set[float]:
    derived: set[float] = set()
    for student in context["students"]:
        attendance = student.get("attendance") or {}
        present, total = attendance.get("present_days"), attendance.get("school_days")
        if present is not None and total:
            pct = present / total * 100
            derived |= {round(pct), round(pct, 1), round(pct, 2), float(total - present)}
        scores = [g["score"] for g in student.get("grades", [])]
        if scores:
            mean = sum(scores) / len(scores)
            derived |= {round(mean, 1), round(mean, 2), round(mean)}
    return derived


def check_output(text: str, context: dict, all_names: list[str]) -> str:
    text = (text or "").strip()
    if not text or len(text) > MAX_ANSWER:
        raise Escalate()
    if "ESCALAR" in text.upper() or URL.search(text) or CODE_LIKE.search(text):
        raise Escalate()
    norm, words = normalize(text), _words(text)
    if NO_INFO.search(norm) or STALL.search(norm):
        raise Escalate()
    own = {normalize(s["name"]) for s in context["students"]}
    own_parts = {part for n in own for part in n.split()}
    for name in all_names:
        full = normalize(name)
        if full in own:
            continue
        first = full.split()[0]
        if full in norm or (first in words and first not in own_parts):
            raise Escalate()
    # Numeric grounding: every figure in the answer must come from the authorised data.
    allowed = _numbers(json.dumps(context, ensure_ascii=False)) | _derived_numbers(context)
    allowed |= {float(round(n)) for n in allowed}  # "98%" for 97,6 is fine
    if not _numbers(text) <= allowed:
        raise Escalate()
    return text
