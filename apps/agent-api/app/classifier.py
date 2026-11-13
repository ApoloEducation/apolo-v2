"""Theme classifier: one decision-model call per guardian message.

Uses a Jev "decision model" (not a chat model): Ollama `d3:2b` via /v1/systemone, or
OpenRouter `typesafe/jev-1.13` via /api/alpha/decisions. Both take a `choice` question and return a
probability for every option, so sensitive themes can win even when they are not the single top answer.
"""
import asyncio
import os
from dataclasses import dataclass, field

import httpx

from .config import MAX_THEMES, load_classifier  # noqa: F401  (MAX_THEMES re-exported)
from .guardrails import Escalate

ESCALATE, DECLINE, ANSWER = "escalate", "decline", "answer"
# Instructions and themes live in configs/classifier.toml (see app/config.py).
# slug -> (action, description shown to the decision model). Descriptions are the "criteria" of the choice question.
THEMES: dict[str, tuple[str, str]] = {slug: (t["action"], t["description"]) for slug, t in load_classifier()["themes"].items()}
INSTRUCTIONS: str = load_classifier()["instructions"]
RETRIES = 2
HISTORY_TURNS = 3
HISTORY_CHARS = 300


@dataclass
class Classification:
    theme: str
    action: str
    confidence: float = 0.0
    probabilities: dict[str, float] = field(default_factory=dict)
    cost: float = 0.0  # USD, when the provider reports it (benchmark only)


def block_threshold() -> float:
    return float(os.getenv("CLASSIFIER_BLOCK_THRESHOLD", "0.30"))


def decide(probabilities: dict[str, float], top: str | None = None, confidence: float = 0.0) -> Classification:
    """Probabilities are added per action. Escalate themes win once together they reach the threshold, even when
    another theme is the top answer. Otherwise decline and answer compete as groups, so a short follow-up split over
    several answer themes ("e em português?") is not refused because one decline theme edged out each of them."""
    known = {slug: float(p) for slug, p in probabilities.items() if slug in THEMES}
    if not known:
        if top in THEMES:
            known = {top: 1.0}
        else:
            raise Escalate("erro-classificador")
    total = {action: sum(p for slug, p in known.items() if THEMES[slug][0] == action) for action in (ESCALATE, DECLINE, ANSWER)}
    if total[ESCALATE] > 0 and total[ESCALATE] >= block_threshold():
        action = ESCALATE
    else:
        action = DECLINE if total[DECLINE] > total[ANSWER] else ANSWER
        if total[action] == 0:  # only escalate themes got any probability, yet below the threshold
            action = ESCALATE
    theme = max((slug for slug in known if THEMES[slug][0] == action), key=known.get)
    return Classification(theme, action, confidence, known)


def build_state(message: str, children: list[str], history: list[dict] | None) -> dict:
    recent = []
    for item in (history or [])[-HISTORY_TURNS:]:
        who = "responsável" if item.get("sender") == "guardian" else "assistente"
        recent.append({"quem": who, "texto": str(item.get("content", ""))[:HISTORY_CHARS]})
    return {"mensagem": message, "filhos_do_responsavel": children, "conversa_recente": recent}


def build_question() -> dict:
    return {"theme": {"type": "choice", "instructions": INSTRUCTIONS, "criteria": {slug: desc for slug, (_, desc) in THEMES.items()}}}


async def _decide(state: dict, questions: dict) -> dict:
    """Send the decision request; returns the response body (`answers`, and `usage` on OpenRouter)."""
    timeout = float(os.getenv("CLASSIFIER_TIMEOUT", "30"))
    if os.getenv("LLM_PROVIDER") == "openrouter":
        payload = {"model": os.getenv("OPENROUTER_CLASSIFIER_MODEL", "typesafe/jev-1.13"), "state": state, "questions": questions}
        headers = {"Authorization": f"Bearer {os.getenv('OPEN_ROUTER_API_KEY', '')}"}
        url = "https://openrouter.ai/api/alpha/decisions"
    else:
        payload = {"model": os.getenv("OLLAMA_CLASSIFIER_MODEL", "d3:2b"), "state": state, "questions": questions}
        headers = {}
        url = f"{os.getenv('OLLAMA_URL', 'http://host.docker.internal:11434')}/v1/systemone"
    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(RETRIES + 1):  # a rate limit or a provider hiccup should not send a normal question to a human
            try:
                response = await client.post(url, json=payload, headers=headers)
                if response.status_code not in (408, 429) and response.status_code < 500:
                    break
            except httpx.TransportError:
                if attempt == RETRIES:
                    raise
            if attempt < RETRIES:
                await asyncio.sleep(0.5 * 2 ** attempt)
        response.raise_for_status()
        return response.json()


async def classify(message: str, children: list[str], history: list[dict] | None = None) -> Classification:
    """Fails closed: any transport or format problem escalates with theme `erro-classificador`."""
    try:
        body = await _decide(build_state(message, children, history), build_question())
        answer = body["answers"]["theme"]
        result = decide(answer.get("probabilities") or {}, answer.get("choice"), float(answer.get("confidence") or 0.0))
        result.cost = float((body.get("usage") or {}).get("cost") or 0.0)
        return result
    except Escalate:
        raise
    except (httpx.HTTPError, KeyError, ValueError, TypeError, AttributeError) as exc:
        raise Escalate("erro-classificador") from exc
