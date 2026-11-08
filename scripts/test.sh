#!/usr/bin/env bash
# Runs the pytest suites in a throwaway virtualenv (.venv, git-ignored). Needs Python 3.12 (uv is used if present).
source "$(dirname "$0")/common.sh"

if [ ! -x .venv/bin/python ]; then
  info "Criando .venv"
  if command -v uv >/dev/null; then uv venv --python 3.12 .venv; else python3.12 -m venv .venv || fail "Python 3.12 não encontrado. Instale (brew install python@3.12) ou use uv."; fi
  if command -v uv >/dev/null; then
    uv pip install -q --python .venv/bin/python -r apps/agent-api/requirements.txt anyio websockets
  else
    .venv/bin/pip install -q -r apps/agent-api/requirements.txt anyio websockets
  fi
fi

for dir in apps/school-data apps/agent-api; do
  info "pytest $dir"
  (cd "$dir" && "$ROOT/.venv/bin/python" -m pytest tests -q)
done
info "pytest benchmarks (modo mock)"
.venv/bin/python -m pytest benchmarks -q
