"""Apolo, the school assistant: a Pydantic AI agent with one tool, scoped read-only SQL.

The flow for one guardian message (`answer`):
  1. fetch the guardian's scope (identity, all student names, SQL schema)  -> fail closed if unreachable
  2. check_input + theme classifier (classifier.py)                         -> escalate / decline before any model call
  3. agent run: the chat model calls `run_sql` as often as it needs, then replies in plain text (or ESCALAR)
  4. check_output (guardrails.py)                                           -> escalate unless every figure is grounded

Adding a tool: write an `@apolo.tool` function below (docstring = what the model reads), put any wording the
model sees in configs/agent.toml, and add a benchmark case.
"""
import json
import logging
import os
import re
from dataclasses import dataclass, field

import httpx
from openai import OpenAIError
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.exceptions import AgentRunError, UserError
from pydantic_ai.messages import (ModelMessage, ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart,
                                  UserPromptPart)
from pydantic_ai.usage import UsageLimits

from . import llm
from .classifier import DECLINE, ESCALATE, classify
from .config import fill, load_agent, render_system_prompt
from .guardrails import ORDINAL, Escalate, _numbers, check_input, check_output, minimal_context

log = logging.getLogger("agent")

MAX_STEPS = 7          # model requests per question, tool rounds and corrective retries included
MAX_RESULT_CHARS = 2500
HISTORY_TURNS = 8
HISTORY_CHARS = 600


@dataclass
class Answer:
    """What the agent decided for one message. `theme` is the classified topic (or the reason, when it escalated)."""
    text: str
    theme: str
    declined: bool = False  # unsafe or off-topic: a fixed polite refusal is sent, nobody is notified
    trace: dict = field(default_factory=dict)  # debugging/benchmark only: classifier probabilities, SQL run, tokens


# ---- school-data service -------------------------------------------------------------------------

def _school_data_url() -> str:
    return os.getenv("SCHOOL_DATA_URL", "http://school-data:8000")


async def _get(path: str):
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(f"{_school_data_url()}{path}")
        response.raise_for_status()
        return response.json()


async def school_context(school_id: str, guardian_id: str) -> dict:
    return await _get(f"/schools/{school_id}/guardians/{guardian_id}/context")


async def school_student_names(school_id: str) -> list[str]:
    return await _get(f"/schools/{school_id}/student-names")


async def school_schema() -> str:
    return (await _get("/sql-schema"))["schema"]


async def school_query(school_id: str, guardian_id: str, sql: str) -> dict:
    """Run one scoped, read-only SELECT. Returns {"error": ...} for rejected SQL so the model can fix it."""
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(f"{_school_data_url()}/schools/{school_id}/guardians/{guardian_id}/query", json={"sql": sql})
    if response.status_code == 400:
        return {"error": response.json().get("detail", "consulta inválida")}
    response.raise_for_status()
    return response.json()


# ---- prompt pieces (text lives in configs/agent.toml, see app/config.py) --------------------------

def few_shot_messages(turns: list[dict]) -> list[ModelMessage]:
    """The sample exchange from agent.toml as real tool-call messages (validated by config.load_agent)."""
    messages: list[ModelMessage] = []
    for i, turn in enumerate(turns):
        call_id = f"exemplo-{i}"
        if turn["kind"] == "user":
            messages.append(ModelRequest(parts=[UserPromptPart(turn["content"])]))
        elif turn["kind"] == "tool_call":
            messages.append(ModelResponse(parts=[ToolCallPart("run_sql", {"sql": turn["sql"]}, tool_call_id=call_id)]))
        elif turn["kind"] == "tool_return":
            previous = f"exemplo-{i - 1}"  # config validation guarantees a tool_call right before
            messages.append(ModelRequest(parts=[ToolReturnPart("run_sql", turn["content"], tool_call_id=previous)]))
        else:
            messages.append(ModelResponse(parts=[TextPart(turn["content"])]))
    return messages


FEW_SHOT = few_shot_messages(load_agent().get("few_shot", []))
NUDGES = load_agent()["nudges"]


def history_messages(history: list[dict] | None) -> list[ModelMessage]:
    """Recent turns as model messages. Guardian -> user prompt; agent/human -> assistant text."""
    messages: list[ModelMessage] = []
    for item in (history or [])[-HISTORY_TURNS:]:
        content = str(item.get("content", ""))[:HISTORY_CHARS]
        if item.get("sender") == "guardian":
            messages.append(ModelRequest(parts=[UserPromptPart(content)]))
        elif item.get("sender") in ("agent", "human"):
            messages.append(ModelResponse(parts=[TextPart(content)]))
    return messages


# ---- the agent -----------------------------------------------------------------------------------

@dataclass
class Deps:
    school_id: str
    guardian_id: str
    prompt: str                    # rendered system prompt (school, students, schema)
    students: str                  # "Ana Souza, Bruno Souza", for the empty-result hint
    data: dict                     # minimal_context + ordinais + resultados: what check_output grounds figures against
    queries: int = 0
    sql_log: list = field(default_factory=list)  # [{"sql", "rows" | "error"}], for the trace
    nudged: set[str] = field(default_factory=set)


apolo = Agent(deps_type=Deps, output_type=str, name="apolo", retries={"tools": 2, "output": MAX_STEPS})


@apolo.instructions
def _instructions(ctx: RunContext[Deps]) -> str:
    # `instructions`, not `system_prompt`: always sent with the current request, even when message_history is passed.
    return ctx.deps.prompt


@apolo.tool
async def run_sql(ctx: RunContext[Deps], sql: str) -> str:
    """Executa uma consulta SELECT (SQLite) no banco da escola, que já mostra só os filhos deste responsável.
    Devolve as colunas e linhas em JSON. Faça médias, porcentagens e contagens dentro do SQL."""
    deps = ctx.deps
    result = await school_query(deps.school_id, deps.guardian_id, sql)  # httpx errors end the run -> Escalate
    log.info("sql=%r result=%r", sql, result)
    deps.queries += 1
    deps.sql_log.append({"sql": sql, **({"error": result["error"]} if "error" in result else {"rows": result["rows"][:10]})})
    if "error" not in result and result["rows"]:
        deps.data["resultados"].append(result["rows"])
    feedback = json.dumps(result, ensure_ascii=False)[:MAX_RESULT_CHARS]
    if "error" not in result and not result["rows"]:
        feedback += fill(NUDGES["empty_result"], students=deps.students)
    return feedback


COMPLETE = re.compile(r"([^\w\s,;:]|\d|\dh)\s*$")  # ends with punctuation, an emoji or a figure; not mid-word


def _unwrap_json(text: str) -> dict | None:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


@apolo.output_validator
def _validate(ctx: RunContext[Deps], text: str) -> str:
    """Corrective retries (one per kind of slip): small models often need them. Guardrails run later, never retried."""
    deps = ctx.deps
    wrapped = _unwrap_json(text.strip())
    if wrapped is not None:
        if isinstance(wrapped.get("answer"), str):
            text = wrapped["answer"]  # old habit: {"answer": "..."} as text
        elif "format" not in deps.nudged:
            deps.nudged.add("format")
            raise ModelRetry(NUDGES["invalid_format"])
    if "cut" not in deps.nudged and "ESCALAR" not in text.upper() and not COMPLETE.search(text):
        deps.nudged.add("cut")  # providers sometimes cut a reply mid-word ("A Bianca est"); ask once for the whole sentence
        raise ModelRetry(NUDGES.get("incomplete", "Resposta incompleta. Escreva a resposta inteira, terminando a frase."))
    if deps.queries == 0 and "numbers" not in deps.nudged and "ESCALAR" not in text.upper() and _numbers(ORDINAL.sub(" ", text)):
        deps.nudged.add("numbers")  # a figure repeated from the conversation, not from the database: it would be blocked
        raise ModelRetry(NUDGES.get("number_without_query", NUDGES["escalate_without_query"]))
    if "ESCALAR" in text.upper() and not deps.data["resultados"]:
        key = f"escalar-{deps.queries}"
        if key not in deps.nudged:
            deps.nudged.add(key)
            raise ModelRetry(NUDGES["escalate_without_query"])
    return text


async def answer(school_id: str, guardian_id: str, question: str, history: list[dict] | None = None) -> Answer:
    try:
        context = await school_context(school_id, guardian_id)
        all_names = await school_student_names(school_id)
        schema = await school_schema()
    except httpx.HTTPError as exc:
        raise Escalate() from exc  # fail closed: no verified scope, no answer
    check_input(question)
    classification = await classify(question, [f"{student['name']} ({student.get('class_name') or 'turma não informada'})" for student in context["students"]], history)
    trace: dict = {"probabilities": {k: round(v, 3) for k, v in classification.probabilities.items() if v >= 0.01}}
    if classification.action == ESCALATE:
        raise Escalate(classification.theme, trace)
    if classification.action == DECLINE:
        return Answer("", classification.theme, declined=True, trace=trace)
    theme = classification.theme
    data = minimal_context(context)
    # Ordinals the guardian typed ("1º bimestre", "9º ano") may be echoed; plain figures ("tirou 9") may not.
    data["ordinais"] = re.findall(r"\d+(?=\s*[º°ª])", question)
    data["resultados"] = []      # every query result: the only source of figures the answer may use
    # Figures typed in the conversation (by anyone) are not facts; check_output lets them be echoed only next to a verified one.
    said = " ".join([question, *(str(item.get("content", "")) for item in (history or [])[-HISTORY_TURNS:])])
    data["numeros_da_pergunta"] = sorted(_numbers(said))
    students = ", ".join(s["name"] for s in data["students"])
    deps = Deps(school_id, guardian_id, render_system_prompt(data["school"] or "escola", students, schema), students, data)
    trace["sql"] = deps.sql_log
    try:
        result = await apolo.run(question, deps=deps, model=llm.build_model(), message_history=[*FEW_SHOT, *history_messages(history)],
                                 usage_limits=UsageLimits(request_limit=MAX_STEPS))
    except (AgentRunError, UserError, httpx.HTTPError, OpenAIError) as exc:
        # model misconfigured, unreachable or misbehaving, retries or step limit exhausted, school-data down mid-run:
        # a human takes over
        log.warning("agent run failed: %r", exc)
        trace["error"] = repr(exc)[:300]
        raise Escalate(trace=trace) from exc
    text = result.output
    usage = result.usage
    trace.update(reply=text, requests=usage.requests, input_tokens=usage.input_tokens, output_tokens=usage.output_tokens)
    try:
        return Answer(check_output(text, data, all_names), theme, trace=trace)
    except Escalate as exc:
        log.info("blocked reply=%r", text)
        trace["blocked_by"] = exc.theme
        raise Escalate("sem-dados" if "ESCALAR" in text.upper() else "resposta-bloqueada", trace) from exc
