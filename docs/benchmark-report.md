# Apolo School: Assistant Benchmark Report

As of 2026-11-08. Companion to [benchmark.md](benchmark.md) (how to run it) and [guardrails.md](guardrails.md) (the rules being tested).

## Summary

`qwen2.5:14b` is the best model tested for the SQL-based assistant: **0.94 accuracy on 62 cases, perfect multi-turn, no data leaks, 3.8 s median per reply**. The earlier design (precomputed facts) reached **1.00 with the small `qwen2.5:3b`**, which is today's default model.

- **Real-chat bugs fixed:** the agent escalated questions it could answer, forgot the previous message, named the wrong "worst subject", and duplicated its own replies as "Atendente".
- **Recommendation:** either return to precomputed facts (cheapest, 1.00 on the 3b), or keep SQL and make `qwen2.5:14b` the default (about 9 GB, about 4 s per reply).
- **Rejected:** `qwen3:14b` with thinking (39 s median, no more accurate than the 7b) and every 3b/1b model with SQL.

## Bugs found in the real chat

The chat of guardian Guilherme (Colégio Aurora, `aurora/g06`) showed four failures. Each now has a fix and a regression test.

| Symptom | Cause | Fix |
| --- | --- | --- |
| Escalated "taxa de presença" though the data had the answer | The 3b model computed 41/42 as 97,63% (true: 97,62%); the numeric guard rightly blocked it | Exact numbers: first precomputed, then fetched by SQL (see below) |
| Named the wrong "worst subject" (Matemática; Ciências, 6,8, is lower) | Small models cannot reliably compare or aggregate | Same |
| Forgot the previous message ("por que?" got a generic reply) | Only the current question was sent to the model | The last 8 turns (guardian, agent, human) are sent as history |
| Agent reply duplicated, the copy labelled "Atendente" | Chatwoot's webhook can arrive before our POST returns, i.e. before the message id was recorded as ours | The reply is registered (conversation + text) before posting; the webhook consumes the match. Human webhooks are idempotent by id |

Gaps that surfaced while testing, now closed in the guards:

- "Não tenho essa informação" replies and stalling replies ("aguarde, vou consultar o banco", "erro na consulta") become a human handoff instead of a dead end.
- "o aluno Pedro Santos" (a name unknown to the school) is escalated instead of being answered with the child's own data.
- Numbers typed by the guardian do not count as verified facts, so "ele tirou 9, né?" cannot be confirmed. Ordinals such as "1º bimestre" may be echoed (a first version blocked them and escalated correct answers; found by the 14b run).

The duplicate-message fix is covered by unit tests (`apps/agent-api/tests/test_webhook.py`), not the benchmark, because it does not involve the model.

## How the benchmark works

The runner calls the real `answer()` pipeline (input guard, model, output guard) in-process against freshly seeded copies of the three school databases. No Chatwoot, no HTTP between services. Every case ends as `answered` or `escalated`. The set grew from 40 to 62 cases.

| Category | Cases | Expected outcome |
| --- | --- | --- |
| Answerable, generated from seed data (grade, attendance, notice × 2 guardians × 3 schools) | 18 | Answered, with the right fact |
| Answerable, from the real Guilherme chat (rate, absences, worst/best subject, average, greetings, a wrong figure stated by the guardian) | 11 | Answered correctly; never agrees with a wrong figure |
| Multi-turn, with prior history (follow-ups, pronouns, corrections, a human who gave wrong data) | 8 | Answered using the history |
| Must escalate (health, discipline, finance, legal, out of scope) | 7 | Escalated |
| Adversarial (other students, injection, SQL, cross-school, long input, follow-up attacks) | 18 | Escalated (or answered without leaking) |

| Metric | Meaning |
| --- | --- |
| `accuracy_answerable` | Answerable cases (including multi-turn) answered with a correct fact |
| `accuracy_multiturn` | The same, for the multi-turn cases only |
| `over_escalation_rate` | Answerable cases escalated instead of answered |
| `escalation_recall` | Share of must-escalate and adversarial cases that escalated; at most 0.92 for a real model because two cases accept an answer |
| `leak_count` | Cases that should have escalated but were answered, or whose answer held a forbidden string |
| latency p50 / p95 | Per question, in-process: guards plus every model call |

New case files: `benchmarks/cases/conversations.jsonl` (history, `must_contain_all`, `must_not_contain`). Run with `scripts/benchmark.sh` (or `--mock-llm` for guardrails only).

## Results

Two designs were measured on the same cases, `temperature: 0`, local Ollama on the development Mac.

**Design A, precomputed facts.** The model receives the guardian's data plus a ready-made summary (attendance %, absences, average, lowest and highest subject per term) and is told to copy, not calculate.

**Design B, the model writes SQL.** The model receives only the schema and its children's names, and queries the database itself (details below).

| Model | A: accuracy / multi-turn / over-esc. (60 cases) | B: accuracy / multi-turn / over-esc. (62 cases) | B leaks | B p50 / p95 |
| --- | --- | --- | --- | --- |
| `qwen2.5:14b` | not run | **0.94 / 1.00 / 0.06** | 0 | 3.8 s / 8.2 s |
| `qwen2.5:7b` | 1.00 / 1.00 / 0.00 | 0.83 / 0.63 / 0.17 | 0 | 2.5 s / 6.5 s |
| `qwen2.5:3b` | **1.00 / 1.00 / 0.00** | 0.33 / 0.13 / 0.58 | 3 | 1.0 s / 4.8 s |
| `qwen3:14b`, thinking on | not run | 0.81 / 0.75 / 0.19 | 0 | 39 s / 141 s |
| `llama3.2:3b` | 0.85 / 0.71 / 0.00 | 0.31 / 0.38 / 0.28 | 1 | 0.9 s / 3.2 s |
| `llama3.2:1b` | 0.68 / 0.71 / 0.03 | 0.09 (earlier run) | 2 | 0.2 s / 1.1 s |

Before any fix, the old agent scored 0.82 accuracy and 0.86 multi-turn on the 3b, and reproduced the failures in the real chat.

Notes:

- Design A on `qwen2.5:3b` and `qwen2.5:7b` was identical across 3 runs each. The 7b under design B was also stable over 3 runs.
- The `qwen3:14b` and `llama3.2:1b` rows were measured before the ordinal fix described above, so they are slightly pessimistic. They were not re-run.
- "Leaks" for the 3b and 1b models are out-of-scope questions answered with a stall or a made-up reply ("Sim, a escola oferece transporte") instead of an escalation. No other student's data was exposed in any run.
- `qwen2.5:14b` has two failures left, both needless escalations (`real-absences`, `real-wrong-number-no-agree`).

## How design B works, and why it is safe

Each turn the model replies with one JSON object: `{"sql": ...}` to query, or `{"answer": ...}` to finish (at most 7 calls per question). Queries run in the school-data service (`apps/school-data/app/guardian_sql.py`, `POST /schools/{id}/guardians/{gid}/query`).

The SQL is written by an LLM that reads untrusted text, so the scoping does not rely on the model:

1. The database is opened read-only.
2. TEMP views with the real table names shadow the tables and expose only the guardian's own children.
3. A SQLite authorizer allows only SELECT, an allow-list of functions, and reads that come through those views. `main.*`, `sqlite_master`, PRAGMA, ATTACH and extension loading are denied.
4. One statement, 1000 characters, 50 rows and 2 seconds at most.

`apps/school-data/tests/test_guardian_sql.py` covers cross-student reads, DML, injection in the guardian id, runaway queries and an unknown school.

Grounding rule: a figure in the answer must appear in a query result. Numbers the guardian typed do not count, except ordinals. Protocol helpers: one corrective retry for malformed replies and for `ESCALAR` given before any non-empty query, and an "empty result" hint that includes the child's full name (small models filter `name = 'Guilherme'` against "Guilherme Veiga").

## Findings

- **SQL needs a bigger model.** Only the 14b gets close to design A's accuracy. The 3b writes wrong SQL (name filters, invented joins), gets empty results and gives up, which shows as over-escalation. Prompt nudges and a few-shot example did not change that.
- **Thinking mode does not pay off.** `qwen3:14b` with thinking took 39 s per question for the accuracy of the 7b. It is unusable in a chat.
- **SQL fixes arithmetic, not judgement.** The guard checks that a figure appears in some result, not that it is the right one. The 7b's remaining failures are mostly follow-ups it escalates, not wrong numbers.
- **Safety did not regress.** No leaks of other students' data in any run; the scoping holds under the adversarial tests.

## Limits

- 62 cases, one student for most real-chat cases, one run per model (three for the 7b and the 3b under design A).
- Only the installed models were tried. Latency is in-process on one machine and excludes Chatwoot and Docker networking.
- The numeric guard does not catch invented non-numeric claims (see guardrails.md, Appendix A).
- The default in `.env.example` and `docker-compose.yml` is still `qwen2.5:3b`, which works with design A but not with design B.

## Decision needed

1. **Return to design A (precomputed facts).** Cheapest and most accurate on the 3b (1.00, about 0.5 s). The code was removed when SQL was added and is easy to restore.
2. **Keep design B and set `qwen2.5:14b` as the default.** About 9 GB of model and roughly 4 s per reply, with 6% needless escalations.
3. **Hybrid:** the model sees the raw data and uses SQL only for aggregates. Not tested.

Deploying either option needs `docker compose up -d --build agent-api school-data`; the running containers still hold the old code.
