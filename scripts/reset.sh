#!/usr/bin/env bash
# Removes containers AND volumes. Current school by default; ALL=1 for every school, including the old shared stack.
source "$(dirname "$0")/common.sh"
if [ "${ALL:-0}" = 1 ]; then
  targets="$(compose_projects with-volumes)"
  [ -n "$targets" ] || { info "Não há dados para apagar."; exit 0; }
  question="Isso apaga TODOS os dados locais de TODAS as escolas (Chatwoot, bancos, conversas). Continuar? [y/N] "
else
  targets="$(env_value COMPOSE_PROJECT_NAME)"
  [ -n "$targets" ] || fail "Nenhuma escola selecionada ainda. Rode 'make start', ou 'make reset ALL=1' para apagar tudo."
  question="Isso apaga todos os dados locais de '$(env_value APOLO_SCHOOL)' (Chatwoot, banco, conversas). Continuar? [y/N] "
fi
read -r -p "$question" answer
[ "$answer" = "y" ] || [ "$answer" = "Y" ] || fail "Cancelado."
for project in $targets; do
  docker compose -p "$project" down -v
done
info "Ambiente zerado. Rode 'make start' para recomeçar."
