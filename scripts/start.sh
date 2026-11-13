#!/usr/bin/env bash
# Set NO_BROWSER=1 to skip opening the browser.
source "$(dirname "$0")/common.sh"
source "$(dirname "$0")/school.sh"

[ -f .env ] || { cp .env.example .env; muted "Criado .env a partir de .env.example"; }
choose_school
info "Escola selecionada: $APOLO_SCHOOL_NAME"

command -v docker >/dev/null || fail "Docker Desktop necessário. Instale em https://www.docker.com/products/docker-desktop/ e rode 'make start' novamente."
if ! docker info >/dev/null 2>&1; then
  if [ "$(uname)" = Darwin ] && [ -d /Applications/Docker.app ]; then
    info "Abrindo o Docker Desktop e aguardando o mecanismo (até 3 minutos)"
    open -a Docker
    wait_for 180 docker info || fail "O Docker Desktop não ficou pronto em 3 minutos. Abra-o manualmente e rode 'make start' novamente."
  else
    fail "Docker Desktop não está em execução. Abra o Docker Desktop e aguarde o mecanismo ficar pronto."
  fi
fi
# LLM=openrouter uses OPENROUTER_MODEL + OPEN_ROUTER_API_KEY from .env instead of local Ollama.
export LLM_PROVIDER="${LLM:-ollama}"
case "$LLM_PROVIDER" in
  openrouter)
    [ -n "$(env_value OPENROUTER_MODEL)" ] || fail "Defina OPENROUTER_MODEL no .env para usar LLM=openrouter."
    [ -n "$(env_value OPEN_ROUTER_API_KEY)" ] || fail "Defina OPEN_ROUTER_API_KEY no .env para usar LLM=openrouter."
    [ -n "$(env_value OPENROUTER_CLASSIFIER_MODEL)" ] || fail "Defina OPENROUTER_CLASSIFIER_MODEL no .env (ex.: typesafe/jev-1.13)."
    info "LLM: OpenRouter ($(env_value OPENROUTER_MODEL); classificador $(env_value OPENROUTER_CLASSIFIER_MODEL))" ;;
  ollama)
    command -v ollama >/dev/null || fail "Ollama necessário. Instale em https://ollama.com (ou 'brew install ollama') e rode 'make start' novamente."

    MODEL="$(env_value OLLAMA_MODEL)"; MODEL="${MODEL:-qwen2.5:3b}"

    if ! ollama_up >/dev/null; then
    info "Iniciando o Ollama"
    nohup ollama serve >"${TMPDIR:-/tmp}/ollama-serve.log" 2>&1 &
    wait_for 30 ollama_up || fail "O Ollama não iniciou em 30 segundos. Execute: ollama serve"
  fi

  CLASSIFIER="$(env_value OLLAMA_CLASSIFIER_MODEL)"; CLASSIFIER="${CLASSIFIER:-d3:2b}"
  for needed in "$MODEL" "$CLASSIFIER"; do
    if ! ollama list | awk 'NR>1 {print $1}' | grep -qx "$needed"; then
      info "Baixando $needed (apenas no primeiro uso; pode levar alguns minutos)"
      ollama pull "$needed"
    fi
  done
    ;;
  *) fail "LLM inválido: $LLM_PROVIDER (use ollama ou openrouter)." ;;
esac

# All stacks publish the same ports, so only one school runs at a time. `down` keeps each school's volumes.
for project in $(compose_projects); do
  if [ "$project" != "$COMPOSE_PROJECT_NAME" ]; then
    info "Interrompendo $project (os dados são preservados)"
    docker compose -p "$project" down
  fi
done

info "Apolo School MVP: iniciando banco de dados e cache"
muted "Aguardando PostgreSQL ficar saudável..."
docker compose up -d --wait --wait-timeout 120 chatwoot-postgres redis || fail "PostgreSQL não ficou saudável em 120 segundos. Execute: docker compose logs chatwoot-postgres"

prepared=0
for attempt in $(seq 1 15); do
  muted "Preparando banco do Chatwoot (tentativa $attempt/15)..."
  if docker compose run --rm --no-deps chatwoot bundle exec rails db:chatwoot_prepare; then prepared=1; break; fi
  sleep 10
done
[ "$prepared" = 1 ] || fail "Não foi possível preparar o banco do Chatwoot. Execute: docker compose logs chatwoot-postgres"

docker compose up --build -d chatwoot chatwoot-worker school-data agent-api messenger

muted "Aguardando serviços ficarem prontos..."
services_ready() { curl -sf -m 2 http://localhost:8000/health && curl -sf -m 2 http://localhost:3000/api; }
wait_for 300 services_ready || fail "Os serviços não ficaram prontos. Execute: docker compose logs agent-api chatwoot"

docker compose run --rm -e BOOTSTRAP=true -e SCHOOL_NAME="$APOLO_SCHOOL_NAME" chatwoot bundle exec rails runner /bootstrap/chatwoot_bootstrap.rb

cat <<MSG

$(printf '\033[32m')Tudo pronto: $APOLO_SCHOOL_NAME$(printf '\033[0m')
  Mini-WhatsApp: http://localhost:5173
  Chatwoot:      http://localhost:3000
  Admin:         admin@escola.local / ApoloLocal2026!
  Atendente:     atendente@escola.local / ApoloLocal2026!
$([ -n "$(env_value ADMIN_TOKEN)" ] && echo "  Painel escola:   http://localhost:8001/admin")

Para parar tudo: make stop
Para trocar de escola: make start
MSG

if [ "${NO_BROWSER:-0}" != 1 ] && command -v open >/dev/null; then
  open http://localhost:5173
  open http://localhost:3000
fi
