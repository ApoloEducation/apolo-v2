#!/usr/bin/env bash
# Runs the benchmark and rebuilds benchmarks/report.html. Usage: scripts/benchmark.sh [--mock-llm | --mock-classifier | --classifier-only]
# [--model X] [--classifier Y] [--runs N] [--filter REGEX]. Defaults come from .env or the environment (LLM_PROVIDER, OPENROUTER_MODEL, ...).
source "$(dirname "$0")/common.sh"
[ -x .venv/bin/python ] || "$ROOT/scripts/test.sh" >/dev/null
export OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"
export OLLAMA_MODEL="${OLLAMA_MODEL:-$(env_value OLLAMA_MODEL)}"
export OLLAMA_CLASSIFIER_MODEL="${OLLAMA_CLASSIFIER_MODEL:-$(env_value OLLAMA_CLASSIFIER_MODEL)}"
export OLLAMA_CLASSIFIER_MODEL="${OLLAMA_CLASSIFIER_MODEL:-d3:2b}"
export LLM_PROVIDER="${LLM_PROVIDER:-$(env_value LLM_PROVIDER)}"
export OPENROUTER_MODEL="${OPENROUTER_MODEL:-$(env_value OPENROUTER_MODEL)}"
export OPENROUTER_CLASSIFIER_MODEL="${OPENROUTER_CLASSIFIER_MODEL:-$(env_value OPENROUTER_CLASSIFIER_MODEL)}"
export OPEN_ROUTER_API_KEY="${OPEN_ROUTER_API_KEY:-$(env_value OPEN_ROUTER_API_KEY)}"
export CLASSIFIER_BLOCK_THRESHOLD="${CLASSIFIER_BLOCK_THRESHOLD:-$(env_value CLASSIFIER_BLOCK_THRESHOLD)}"
exec .venv/bin/python benchmarks/run_benchmark.py "$@"
