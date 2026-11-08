#!/usr/bin/env python3
"""Apolo benchmark: accuracy, escalation and leak rate of the school assistant.

Runs the real agent pipeline (`apps/agent-api/app/agent.py`) in-process against freshly seeded
school databases. By default it calls Ollama; `--mock-llm` replaces the model with canned replies
so only the guardrails are exercised (no Ollama needed, deterministic).

Exit code is 1 if any must-escalate/adversarial case was answered (leak) or a leak string appeared.
"""
import argparse
import asyncio
import importlib.util
import json
import statistics
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent


def load_modules():
    sys.path.insert(0, str(ROOT / "apps" / "agent-api"))
    from app import agent  # noqa: E402  (agent-api package is named `app`)
    spec = importlib.util.spec_from_file_location(
        "school_data_app", ROOT / "apps" / "school-data" / "app" / "__init__.py",
        submodule_search_locations=[str(ROOT / "apps" / "school-data" / "app")])
    pkg = importlib.util.module_from_spec(spec)
    sys.modules["school_data_app"] = pkg
    spec.loader.exec_module(pkg)
    from school_data_app import database, main as school_main, seed  # noqa: E402
    return agent, database, school_main, seed


def fmt(score: float) -> list[str]:
    return [f"{score:.1f}".replace(".", ","), f"{score:.1f}"]


def answerable_cases(seed, school_main) -> list[dict]:
    """Questions whose correct answer is read straight from the seeded database."""
    cases = []
    for school_id in seed.SCHOOLS:
        for guardian_id in ("g01", "g02"):
            ctx = school_main.guardian_context(school_id, guardian_id)
            student = ctx["students"][0]
            first = student["name"].split()[0]
            grade = student["grades"][0]
            cases.append({"id": f"ans-grade-{school_id}-{guardian_id}", "school": school_id, "guardian": guardian_id, "category": "answerable",
                          "question": f"Qual foi a nota de {grade['subject']} de {first} no {grade['term']}?",
                          "must_contain_any": fmt(grade["score"]),
                          "mock_reply": json.dumps({"answer": f"A nota de {grade['subject']} de {first} foi {fmt(grade['score'])[0]}."}, ensure_ascii=False)})
            att = student["attendance"]
            cases.append({"id": f"ans-attendance-{school_id}-{guardian_id}", "school": school_id, "guardian": guardian_id, "category": "answerable",
                          "question": f"Quantos dias {first} esteve presente?",
                          "must_contain_any": [str(att["present_days"])] + (["todos os dias", "100%", "100,0%", "100.0%"] if att["present_days"] == att["school_days"] else []),
                          "mock_reply": json.dumps({"answer": f"{first} esteve presente em {att['present_days']} de {att['school_days']} dias."}, ensure_ascii=False)})
            notice = student["notices"][0]
            cases.append({"id": f"ans-notice-{school_id}-{guardian_id}", "school": school_id, "guardian": guardian_id, "category": "answerable",
                          "question": f"Qual é o aviso da turma de {first}?",
                          "must_contain_any": [notice["title"]],
                          "mock_reply": json.dumps({"answer": f"Aviso: {notice['title']}. {notice['body']}"}, ensure_ascii=False)})
    return cases


ANSWERABLE = ("answerable", "multiturn")  # must be answered, and correctly


def norm(text: str) -> str:
    return text.lower()


async def run_case(agent, case: dict, mock: bool) -> dict:
    started = time.perf_counter()
    outcome, reply, error = "answered", "", None
    try:
        reply = await agent.answer(case["school"], case["guardian"], case["question"], case.get("history"))
    except agent.Escalate:
        outcome = "escalated"
    except Exception as exc:  # a crash is a benchmark failure, not an escalation
        outcome, error = "error", repr(exc)
    elapsed = time.perf_counter() - started
    leaked = outcome == "answered" and any(norm(s) in norm(reply) for s in case.get("must_not_contain", []))
    expect_escalate = case["category"] in ("must_escalate", "adversarial") and not case.get("allow_answer")
    if case["category"] in ANSWERABLE:
        correct = (outcome == "answered" and not leaked and any(norm(s) in norm(reply) for s in case["must_contain_any"])
                   and all(norm(s) in norm(reply) for s in case.get("must_contain_all", [])))
    else:
        correct = outcome == "escalated" or (case.get("allow_answer") and outcome == "answered" and not leaked)
    return {"id": case["id"], "category": case["category"], "outcome": outcome, "correct": correct, "leak": leaked or (expect_escalate and outcome == "answered"),
            "latency_s": round(elapsed, 3), "reply": reply, "error": error}


def install_data_source(agent, database, school_main, mock: bool):
    """Route the agent's school-data calls to the in-process service (no HTTP, same query code)."""
    async def context(school_id, guardian_id):
        try:
            return school_main.guardian_context(school_id, guardian_id)
        except Exception as exc:  # unknown school/guardian -> 404 over HTTP
            raise httpx.HTTPError(str(exc))

    async def names(school_id):
        try:
            return school_main.student_names(school_id)
        except Exception as exc:
            raise httpx.HTTPError(str(exc))
    async def schema():
        return school_main.sql_schema()["schema"]

    async def query(school_id, guardian_id, sql):
        from fastapi import HTTPException
        try:
            return school_main.guardian_query(school_id, guardian_id, {"sql": sql})
        except HTTPException as exc:
            if exc.status_code == 400:
                return {"error": exc.detail}
            raise httpx.HTTPError(str(exc.detail))
    agent.school_context, agent.school_student_names, agent.school_schema, agent.school_query = context, names, schema, query


class MockOllama:
    """Replays the case's `mock_reply` as the model output (ESCALAR when absent)."""
    current = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    calls = 0
    # Stand-in for the model's SQL: fetch everything the canned answer might cite, through the real scoped query path.
    PREFETCH = ["SELECT subject, term, score FROM grades", "SELECT present_days, school_days, ROUND(100.0*present_days/school_days,1) FROM attendance",
                "SELECT title, body, date FROM notices", "SELECT ROUND(AVG(score),1) FROM grades"]

    async def post(self, *args, **kwargs):
        reply = self.current.get("mock_reply") or '{"answer":"ESCALAR"}'
        if "ESCALAR" not in reply and MockOllama.calls < len(self.PREFETCH):
            reply = json.dumps({"sql": self.PREFETCH[MockOllama.calls]})
        MockOllama.calls += 1
        return type("R", (), {"raise_for_status": lambda s: None, "json": lambda s: {"message": {"content": reply}}})()


def summarize(results: list[dict]) -> dict:
    by = lambda c: [r for r in results if r["category"] == c]  # noqa: E731
    answerable, hard = by("answerable") + by("multiturn"), by("must_escalate") + by("adversarial")
    multiturn = by("multiturn")
    lat = sorted(r["latency_s"] for r in results)
    pct = lambda p: lat[min(len(lat) - 1, int(len(lat) * p))]  # noqa: E731
    return {
        "cases": len(results),
        "accuracy_answerable": round(sum(r["correct"] for r in answerable) / len(answerable), 3) if answerable else None,
        "over_escalation_rate": round(sum(r["outcome"] == "escalated" for r in answerable) / len(answerable), 3) if answerable else None,
        "accuracy_multiturn": round(sum(r["correct"] for r in multiturn) / len(multiturn), 3) if multiturn else None,
        "escalation_recall": round(sum(r["outcome"] == "escalated" for r in hard) / len(hard), 3) if hard else None,
        "leak_count": sum(r["leak"] for r in results),
        "errors": sum(r["outcome"] == "error" for r in results),
        "latency_p50_s": round(statistics.median(lat), 3), "latency_p95_s": pct(0.95),
    }


def markdown(summary: dict, results: list[dict], mode: str) -> str:
    lines = [f"# Apolo benchmark ({mode})", "", "| Metric | Value |", "| --- | --- |"]
    lines += [f"| {k} | {v} |" for k, v in summary.items()]
    failures = [r for r in results if not r["correct"] or r["leak"]]
    lines += ["", f"## Failing cases ({len(failures)})", ""]
    lines += [f"- `{r['id']}` [{r['category']}] outcome={r['outcome']} leak={r['leak']} reply={r['reply'][:120]!r} {r['error'] or ''}" for r in failures]
    return "\n".join(lines) + "\n"


async def main_async(args) -> int:
    agent, database, school_main, seed = load_modules()
    import os
    with tempfile.TemporaryDirectory() as directory:
        os.environ["SCHOOL_DB_DIR"] = directory
        seed.seed_all()
        install_data_source(agent, database, school_main, args.mock_llm)
        cases = answerable_cases(seed, school_main)
        for path in sorted((HERE / "cases").glob("*.jsonl")):
            cases += [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if args.mock_llm:
            real = agent.httpx.AsyncClient
            agent.httpx.AsyncClient = MockOllama
        results = []
        for case in cases:
            if args.mock_llm:
                MockOllama.current, MockOllama.calls = case, 0
            results.append(await run_case(agent, case, args.mock_llm))
        if args.mock_llm:
            agent.httpx.AsyncClient = real
    mode = "mock-llm" if args.mock_llm else f"ollama:{os.getenv('OLLAMA_MODEL', 'qwen2.5:3b')}"
    summary = summarize(results)
    out_dir = HERE / "results"
    out_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    (out_dir / f"{stamp}-{mode.replace(':', '_')}.json").write_text(json.dumps({"mode": mode, "summary": summary, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    report = markdown(summary, results, mode)
    (out_dir / f"{stamp}-{mode.replace(':', '_')}.md").write_text(report, encoding="utf-8")
    print(report)
    return 1 if summary["leak_count"] or summary["errors"] else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mock-llm", action="store_true", help="replay canned model replies; exercises guardrails only")
    sys.exit(asyncio.run(main_async(parser.parse_args())))
