import json
import os
import re

import httpx

SENSITIVE = re.compile(r"(saúde|médic|remédio|laudo|diagnóst|bullying|disciplina|violên|juríd|financeir|mensalidade|pagamento)", re.I)
OTHER_STUDENT = re.compile(r"(outro aluno|colega|aluno de outra turma|[A-ZÁÀÃÂÉÊÍÓÔÕÚÇ][a-záàãâéêíóôõúç]+\s+[A-ZÁÀÃÂÉÊÍÓÔÕÚÇ][a-záàãâéêíóôõúç]+)")


class Escalate(Exception):
    pass


async def school_context(guardian_id: str) -> dict:
    url = os.getenv("SCHOOL_DATA_URL", "http://school-data:8000")
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(f"{url}/guardians/{guardian_id}/context")
        response.raise_for_status()
        return response.json()


def guard(question: str, context: dict) -> None:
    if SENSITIVE.search(question):
        raise Escalate()
    student_names = [student["name"].lower() for student in context["students"]]
    words = question.lower()
    # A full name not belonging to the responsible is never sent to the model.
    if OTHER_STUDENT.search(question) and not any(name in words for name in student_names):
        raise Escalate()


SYSTEM = """Você é o assistente virtual da Escola Horizonte. Responda SEMPRE em formato JSON com o seguinte formato:
{"answer": "<sua resposta em português do Brasil em linguagem natural>"}

Regras:
- Responda em português do Brasil em linguagem natural, amigável, acolhedora e concisa, como um atendente escolar humano.
- Use exclusivamente as informações fornecidas em DADOS AUTORIZADOS sobre os filhos do responsável.
- Nunca exiba código ou JSON bruto dentro do texto da resposta.
- Se as informações não responderem à pergunta ou a informação não estiver disponível, devolva exatamente: {"answer": "ESCALAR"}"""


async def answer(guardian_id: str, question: str) -> str:
    context = await school_context(guardian_id)
    guard(question, context)
    payload = {
        "model": os.getenv("OLLAMA_MODEL", "qwen2.5:3b"),
        "stream": False,
        "format": "json",
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"DADOS AUTORIZADOS:\n{json.dumps(context, ensure_ascii=False)}\n\nPERGUNTA: {question}"},
        ],
    }
    try:
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(f"{os.getenv('OLLAMA_URL', 'http://host.docker.internal:11434')}/api/chat", json=payload)
            response.raise_for_status()
            raw = response.json()["message"]["content"]
    except httpx.HTTPError as exc:
        raise Escalate() from exc
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict) and "answer" in parsed:
            text = str(parsed["answer"])
        else:
            text = raw
    except json.JSONDecodeError:
        text = raw
    if not text or "ESCALAR" in text.upper() or len(text) > 1200 or text.strip().startswith("{"):
        raise Escalate()
    return text.strip()
