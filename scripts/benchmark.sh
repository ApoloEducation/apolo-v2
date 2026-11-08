#!/usr/bin/env bash
# Runs the benchmark. Usage: scripts/benchmark.sh [--mock-llm]. Model comes from OLLAMA_MODEL (.env or env).
source "$(dirname "$0")/common.sh"
[ -x .venv/bin/python ] || "$ROOT/scripts/test.sh" >/dev/null
export OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"
export OLLAMA_MODEL="${OLLAMA_MODEL:-$(env_value OLLAMA_MODEL)}"
exec .venv/bin/python benchmarks/run_benchmark.py "$@"
