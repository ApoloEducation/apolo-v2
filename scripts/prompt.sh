#!/usr/bin/env bash
# Prints the rendered agent system prompt and the classifier themes, read from ./configs.
source "$(dirname "$0")/common.sh"

PYTHON="$ROOT/.venv/bin/python"
[ -x "$PYTHON" ] || PYTHON="$(command -v python3.12 || command -v python3)"
cd apps/agent-api && "$PYTHON" -m app.config
