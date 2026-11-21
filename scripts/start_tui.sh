#!/usr/bin/env bash
# Entry point of `make start`: installs `rich` into .venv if needed, then runs the guided launcher.
source "$(dirname "$0")/common.sh"

if [ ! -x .venv/bin/python ]; then
  info "Criando .venv"
  if command -v uv >/dev/null; then uv venv --python 3.12 .venv; else python3 -m venv .venv || fail "Python 3 não encontrado."; fi
fi
if ! .venv/bin/python -c "import rich" 2>/dev/null; then
  info "Instalando rich"
  if command -v uv >/dev/null; then uv pip install -q --python .venv/bin/python rich; else .venv/bin/pip install -q rich; fi
fi
exec .venv/bin/python scripts/start_tui.py
