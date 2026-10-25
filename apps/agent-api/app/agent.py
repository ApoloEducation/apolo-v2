import json
import logging
import os
import re
import unicodedata
from datetime import date
from pathlib import Path

import httpx

log = logging.getLogger("apolo.agent")

CATEGORIES = ("financeiro", "saude", "disciplina", "pedagogico", "documentos", "eventos_calendario", "outros")
CATEGORY_KEYWORDS = (
    ("financeiro", re.compile(r"(financeir|mensalidade|pagamento|boleto|desconto|bolsa)", re.I)),
    ("saude", re.compile(r"(saúde|médic|remédio|laudo|diagnóst|alergia|doente)", re.I)),
    ("disciplina", re.compile(r"(comport|indisciplin|bagunç|bullying|disciplina|violên|briga|advertência|suspensão|juríd)", re.I)),
    ("pedagogico", re.compile(r"(nota|prova|frequência|falta|boletim|desempenho|reprov)", re.I)),
    ("documentos", re.compile(r"(declaração|histórico|transferência|matrícula|documento|atestado)", re.I)),
    ("eventos_calendario", re.compile(r"(reunião|evento|feriado|calendário|passeio|festa|horário)", re.I)),
)
SENSITIVE = re.compile(r"(comport|indisciplin|bagunç|saúde|médic|remédio|laudo|diagnóst|febre|doente|doença|machuc|acidente|emerg[eê]ncia|urgente|vômit|alerg|bullying|disciplina|violên|juríd|financeir|mensalidade|pagamento)", re.I)
NAME_PAIR = r"[A-ZÁÀÃÂÉÊÍÓÔÕÚÇ][a-záàãâéêíóôõúç]+\s+[A-ZÁÀÃÂÉÊÍÓÔÕÚÇ][a-záàãâéêíóôõúç]+"
OTHER_STUDENT = re.compile(rf"(outro aluno|colega|coleguinha|aluno de outra turma|{NAME_PAIR})")
HUMAN_REQUEST = re.compile(
    r"(\b(humano|atendente|pessoa (real|de verdade))\b"
    r"|\b(falar|conversar|passa\w*|passe|transfer\w*|encaminh\w*|chama\w*|ligar|ligue)\b.{0,40}\b(professor\w*|coordena\w*|diretor\w*|secret[aá]ri\w*|funcion[aá]ri\w*|alguém|alguem)\b)", re.I)
# Things the bot can never actually do; if the model promises them, the parent must really be handed off.
PROMISE = re.compile(r"(vou\s+(te\s+|lhe\s+)?(encaminhar|enviar|passar|transferir|acelerar|chamar)|(enviar|encaminhar|repassar)\s+(o\s+)?seu\s+(pedido|mensagem)|passar\s+(você|voce)\s+para|transferir\s+(você|voce))", re.I)
# Topics the school data can never settle (forecasts, exams, school rules): always a human's call.
NEEDS_HUMAN = re.compile(r"(feriado|recesso|greve|férias|aprova|reprova|passar de ano|passa de ano|\b(vai|para|ir[aá]|consegue|conseguir[aá]?) passar\b|recupera[cç][aã]o|\bprovas?\b.{0,25}\b(amanh[aã]|semana|marcad\w*|quando|data|dia)\b|\b(quando|data|dia)\b.{0,25}\bprovas?\b|recreio|intervalo)", re.I)
SMALLTALK_WORDS = set("oi oii oie olá ola opa e aí ai bom boa dia tarde noite tudo bem td kk kkk kkkk haha hehe como vai vc você voce está esta obrigado obrigada obrigadão valeu brigado brigada muito ok certo beleza blz tá ta entendi perfeito show legal".split())
THANKS_WORDS = set("obrigado obrigada obrigadão valeu brigado brigada ok certo beleza blz tá ta entendi perfeito show legal".split())
SCOPE = re.compile(
    r"(nota|boletim|desempenho|\bprovas?\b|\baulas?\b|hor[aá]rio|falt|frequ[eê]ncia|presen[cç]|ausen|professor|aviso|reuni[aã]o|feira|evento|calend|turma|mat[eé]ria|disciplina"
    r"|matem|portugu|ci[eê]ncias|hist[oó]ria|geografia|ingl[eê]s|\bartes?\b|educa[cç][aã]o f[ií]sica|bimestre|avalia|projeto|entrega|segunda|ter[cç]a|quarta|quinta|sexta"
    r"|como (est[aá]|vai|anda|est[aã]o)|indo\b)", re.I)
# The model telling the parent "I have no information / ask someone else" is a handoff in disguise.
REFUSAL = re.compile(r"(n[ãa]o (h[aá]|tenho|possuo|consta|encontr)\w*\s+(informa|registro|dado)|n[ãa]o consta|base de dados|entre em contato|verifique (diretamente )?(com|junto)|diretamente com|dados autorizados|nos dados|\b(geralmente|normalmente|costuma|em geral|provavelmente|possivelmente|talvez)\b|n[ãa]o posso permitir|a escola (n[ãa]o )?(permite|autoriza|exige)|pol[ií]tica da escola|calend[aá]rio escolar|n[ãa]o h[aá] (nenhum|nenhuma|aula|disciplina|atividade)|ainda n[ãa]o (foi|h[aá]|tem|est[aá])|ser[aá] (marcad|agendad|divulgad)|receber[aá] (uma )?notifica)", re.I)
# Words that tie a message to the child even when no school keyword is present ("fala mais sobre ela").
CHILD_WORDS = re.compile(r"(mais sobre|o que mais|me (fala|conta|diz) mais|conta mais|outras? informa|resumo|tudo (o )?que (sabe|voc[eê] sabe|vc sabe))", re.I)
# Questions whose answer differs from one child to the next (as opposed to school-wide notices).
STUDENT_SPECIFIC = re.compile(r"(nota|boletim|desempenho|\bprovas?\b|falt|frequ[eê]ncia|presen[cç]|ausen|\baulas?\b|hor[aá]rio|professor|turma|mat[eé]ria|disciplina|bimestre|matem|portugu|ci[eê]ncias|como (est[aá]|vai|anda)|indo\b|mais sobre|o que mais|me (fala|conta|diz) mais|resumo)", re.I)
ALL_CHILDREN = re.compile(r"\b(dois|duas|ambos|ambas|todos|todas|filhos|filhas|irm[aã]os|crian[cç]as|cada um)\b", re.I)
CLARIFY = "Você tem mais de um filho na escola: {names}. De qual deles você está falando?"
GENERIC_STEMS = {"aluno", "alunos", "quais", "quand", "sobre", "ainda", "estao", "entre", "porqu", "todos", "podem", "devem", "tempo", "fazer", "trazer", "levar", "dia"}
NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
NOT_A_PERSON = re.compile(r"Escola\s+Horizonte", re.I)

PROMPT_PATH = Path(os.getenv("SYSTEM_PROMPT_PATH", Path(__file__).parent / "prompts" / "system_pt.md"))


class Escalate(Exception):
    def __init__(self, reason: str = "", category: str = "outros", question: str = ""):
        super().__init__(reason)
        self.reason = reason
        self.category = category if category in CATEGORIES else "outros"
        self.question = question


def categorize(text: str) -> str:
    for category, pattern in CATEGORY_KEYWORDS:
        if pattern.search(text):
            return category
    return "outros"


async def school_context(guardian_id: str) -> dict:
    url = os.getenv("SCHOOL_DATA_URL", "http://school-data:8000")
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(f"{url}/guardians/{guardian_id}/context")
        response.raise_for_status()
        return response.json()


def other_first_names(roster: list[dict] | None, context: dict) -> set[str]:
    own = {s["name"].split()[0].lower() for s in context["students"]}
    return {r["name"].split()[0].lower() for r in roster or []} - own


def guard(question: str, context: dict, roster: list[dict] | None = None) -> None:
    if HUMAN_REQUEST.search(question):
        raise Escalate("pedido explícito de atendimento humano", categorize(question), question)
    if NEEDS_HUMAN.search(question):
        raise Escalate("assunto que os dados da escola não respondem", categorize(question), question)
    if SENSITIVE.search(question):
        raise Escalate("assunto sensível", categorize(question), question)
    words_in_question = set(re.findall(r"\w+", question.lower()))
    if words_in_question & other_first_names(roster, context):
        raise Escalate("menção a outro aluno", categorize(question), question)
    student_names = [student["name"].lower() for student in context["students"]]
    words = question.lower()
    # A full name not belonging to the responsible is never sent to the model.
    if OTHER_STUDENT.search(NOT_A_PERSON.sub("", question)) and not any(name in words for name in student_names):
        raise Escalate("menção a outro aluno", categorize(question), question)


async def student_roster() -> list[dict]:
    """Every student of the school, used only to recognise names the parent is not allowed to ask about."""
    try:
        url = os.getenv("SCHOOL_DATA_URL", "http://school-data:8000")
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(f"{url}/students")
            response.raise_for_status()
            return response.json()
    except httpx.HTTPError as exc:
        log.warning("não foi possível carregar a lista de alunos: %s", exc)
        return []


def smalltalk_reply(question: str, guardian_name: str) -> str | None:
    """Greetings and thanks never need the model, which tends to volunteer grades and notices."""
    tokens = re.findall(r"[\wçãõáéíóúâêô]+", question.lower())
    if not tokens or len(tokens) > 6 or not set(tokens) <= SMALLTALK_WORDS:
        return None
    if set(tokens) & THANKS_WORDS:
        return "Por nada! Estou à disposição se precisar de mais alguma coisa."
    return f"Olá, {guardian_name.split()[0]}! Sou o assistente virtual da Escola Horizonte. Como posso ajudar?"


class Clarify(Exception):
    """Not a handoff: the bot just needs the parent to say which child the question is about."""


def plain(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn")


def data_vocabulary(context: dict) -> set[str]:
    """5-letter stems of what the school data talks about, so any new notice or subject is searchable without touching the code."""
    terms: list[str] = []
    for student in context["students"]:
        terms += [student["name"], student.get("class_name", ""), student.get("homeroom_teacher", "")]
        terms += [g["subject"] for g in student.get("grades", [])]
        for slot in student.get("schedule", []):
            terms += [slot["subject"], slot["teacher"]]
        for notice in student.get("notices", []):
            terms += [notice["title"], notice["body"]]
    return {w[:5] for term in terms for w in re.findall(r"[a-z]{5,}", plain(term))} - GENERIC_STEMS


def in_school_scope(question: str, context: dict) -> bool:
    """Questions that mention nothing the school data covers (e.g. "capital da França", "ônibus") go to a human."""
    words = set(re.findall(r"\w+", question.lower()))
    names = {n for s in context["students"] for n in s["name"].lower().split()}
    if SCOPE.search(question) or CHILD_WORDS.search(question) or words & names:
        return True
    return bool({w[:5] for w in re.findall(r"[a-z]{5,}", plain(question))} & data_vocabulary(context))


def first_name(student: dict) -> str:
    return student["name"].split()[0]


def named_students(text: str, students: list[dict]) -> list[dict]:
    words = set(re.findall(r"\w+", plain(text)))
    return [s for s in students if plain(first_name(s)) in words]


def pick_student(question: str, context: dict, history: list[dict] | None) -> tuple[dict, str]:
    """With siblings the data sent to the model is narrowed to the child being discussed; if unclear, ask the parent."""
    students = context["students"]
    if len(students) < 2:
        return context, question
    history = history or []
    clarifying = bool(history) and history[-1]["sender"] == "agent" and history[-1]["content"].startswith(CLARIFY.split("{")[0])
    named = named_students(question, students)
    if named and clarifying and len(question.split()) <= 5:
        # The parent only answered "a Júlia": rejoin it with the question that was waiting for a child.
        earlier = next((h["content"] for h in reversed(history[:-1]) if h["sender"] == "guardian"), "")
        question = f"{earlier} (sobre {first_name(named[0])})"
    elif not named:
        if ALL_CHILDREN.search(question) or not STUDENT_SPECIFIC.search(question):
            return context, question
        for item in reversed(history[-6:]):  # "e a frequência dela?" keeps talking about the last child mentioned
            recent = named_students(item["content"], students)
            if len(recent) == 1:
                named = recent
                break
        if not named:
            raise Clarify(CLARIFY.format(names=" e ".join(first_name(s) for s in students)))
    return {**context, "students": named}, question


def numbers_in(text: str) -> set[float]:
    return {float(n.replace(",", ".")) for n in NUMBER.findall(text)}


def pt_date(value: str) -> str:
    """Notices are edited by hand in the admin UI, so accept 2026-10-26 and 26-10-2026 alike."""
    parts = re.split(r"[-/]", value.strip())
    if len(parts) == 3 and len(parts[0]) == 4:
        parts.reverse()
    return "/".join(parts) if len(parts) == 3 else value


def render_context(context: dict) -> str:
    """Plain Portuguese lines are much easier for a small model to read correctly than nested JSON."""
    lines = [f"Hoje é {date.today().strftime('%d/%m/%Y')}.", f"Responsável: {(context.get('guardian') or {}).get('name', '')}"]
    for student in context["students"]:
        lines.append(f"\nAluno: {student['name']} - turma {student.get('class_name', '?')} ({student.get('shift', '?')}), professor responsável: {student.get('homeroom_teacher', '?')}")
        if student.get("grades"):
            lines.append("Notas: " + "; ".join(f"{g['subject']} {g['score']} ({g.get('term', '')})" for g in student["grades"]))
        attendance = student.get("attendance")
        if attendance:
            present, total = attendance.get("present_days", 0), attendance.get("school_days", 0)
            absences = attendance.get("absences", total - present)
            percent = round(100 * present / total, 1) if total else 0
            lines.append(f"Frequência: {present} presenças em {total} dias letivos; faltas: {absences}; presença: {percent}%")
        for slot in student.get("schedule", []):
            lines.append(f"Aula: {slot['weekday']} {slot['time']} - {slot['subject']} (professor(a) {slot['teacher']})")
        for notice in student.get("notices", []):
            lines.append(f"Aviso ({pt_date(notice['date'])}): {notice['title']} - {notice['body'].strip()}")
    return "\n".join(lines)


def system_prompt(guardian_name: str) -> str:
    return PROMPT_PATH.read_text(encoding="utf-8").replace("{guardian_name}", guardian_name)


def build_messages(context: dict, question: str, history: list[dict] | None, guardian_name: str) -> list[dict]:
    messages = [{"role": "system", "content": system_prompt(guardian_name)}]
    for item in (history or [])[-6:]:
        role = "user" if item["sender"] == "guardian" else "assistant"
        messages.append({"role": role, "content": item["content"]})
    messages.append({"role": "user", "content": f"DADOS AUTORIZADOS:\n{render_context(context)}\n\nPERGUNTA DO RESPONSÁVEL: {question}"})
    return messages


async def answer(guardian_id: str, question: str, history: list[dict] | None = None) -> str:
    context = await school_context(guardian_id)
    guard(question, context, await student_roster())
    guardian_name = (context.get("guardian") or {}).get("name", "responsável")
    canned = smalltalk_reply(question, guardian_name)
    if canned:
        return canned
    if not in_school_scope(question, context):
        raise Escalate("assunto fora dos dados da escola", categorize(question), question)
    try:
        context, question = pick_student(question, context, history)
    except Clarify as clarify:
        return str(clarify)
    payload = {
        "model": os.getenv("OLLAMA_MODEL", "qwen2.5:3b"),
        "stream": False,
        "format": "json",
        "options": {"temperature": float(os.getenv("OLLAMA_TEMPERATURE", "0.2"))},
        "messages": build_messages(context, question, history, guardian_name),
    }
    try:
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(f"{os.getenv('OLLAMA_URL', 'http://host.docker.internal:11434')}/api/chat", json=payload)
            response.raise_for_status()
            raw = response.json()["message"]["content"]
    except httpx.HTTPError as exc:
        log.error("Ollama indisponível (%s: %s); escalando", type(exc).__name__, exc)
        raise Escalate(f"falha no modelo: {type(exc).__name__}", categorize(question), question) from exc
    log.info("LLM raw output: %s", raw)
    category, wants_escalation = categorize(question), False
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        text = str(parsed.get("answer") or "")
        wants_escalation = parsed.get("escalate") is True
        if parsed.get("category") in CATEGORIES and parsed["category"] != "outros":
            category = parsed["category"]
    else:
        text = raw
    text = text.strip()
    if PROMISE.search(text):
        log.warning("modelo prometeu encaminhar por conta própria: %s", text)
        wants_escalation = True
    if REFUSAL.search(text) or (re.search(r"\bm[eé]dia", text, re.I) and not re.search(r"m[eé]dia", question, re.I)):
        log.warning("resposta recusada/inventada pelo modelo: %s", text)
        wants_escalation = True
    allowed = numbers_in(json.dumps(context, ensure_ascii=False)) | numbers_in(render_context(context)) | numbers_in(question) | numbers_in(" ".join(h["content"] for h in history or []))
    invented = numbers_in(text) - allowed
    if invented:
        log.warning("resposta com números inexistentes nos dados %s: %s", sorted(invented), text)
        wants_escalation = True
    if wants_escalation or not text or text.upper() == "ESCALAR" or len(text) > 1200 or text.startswith("{"):
        raise Escalate("modelo não soube responder", category, question)
    return text
