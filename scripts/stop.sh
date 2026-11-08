#!/usr/bin/env bash
# macOS/Linux equivalent of stop.ps1. Volumes are preserved.
source "$(dirname "$0")/common.sh"
if ! docker info >/dev/null 2>&1; then
  info "Docker não está em execução; não há nada para interromper."
  exit 0
fi
for project in $(compose_projects); do
  docker compose -p "$project" down
done
info "Serviços interrompidos. Os dados locais foram preservados nos volumes Docker."
