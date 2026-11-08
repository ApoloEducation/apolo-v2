# Shared helpers for the Makefile commands. Sourced, not executed.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# Docker Desktop on macOS does not always put its CLI on the PATH of non-login shells.
export PATH="$PATH:/usr/local/bin:/opt/homebrew/bin:/Applications/Docker.app/Contents/Resources/bin"

info()  { printf '\033[36m==> %s\033[0m\n' "$*"; }
muted() { printf '\033[2m%s\033[0m\n' "$*"; }
fail()  { printf '\033[33m%s\033[0m\n' "$*" >&2; exit 1; }

# Value of KEY in .env (empty if unset).
env_value() { grep -E "^$1=" .env 2>/dev/null | tail -1 | cut -d= -f2- || true; }

# Poll `cmd...` every 3 s until it succeeds or $1 seconds pass.
wait_for() {
  local timeout="$1"; shift
  local deadline=$((SECONDS + timeout))
  until "$@" >/dev/null 2>&1; do
    [ "$SECONDS" -lt "$deadline" ] || return 1
    sleep 3
  done
}

# Writes KEY=VALUE to .env, replacing any earlier value and keeping everything else.
set_env_value() {
  local kept
  kept="$(grep -vE "^$1=" .env || true)"
  printf '%s\n%s=%s\n' "$kept" "$1" "$2" | sed '/./,$!d' > .env
}

# Every school is its own Compose project (containers + volumes), so nothing is shared between schools.
# The bare prefix is the old single stack that held all schools.
PROJECT_PREFIX=apolo-school-mvp
compose_projects() {
  { docker ps -a --filter label=com.docker.compose.project --format '{{.Label "com.docker.compose.project"}}'
    if [ "${1:-}" = with-volumes ]; then docker volume ls --filter label=com.docker.compose.project --format '{{.Label "com.docker.compose.project"}}'; fi
  } | sort -u | grep -E "^$PROJECT_PREFIX(-|\$)" || true
}

ollama_up() { curl -sf -m 2 http://127.0.0.1:11434/api/tags; }
