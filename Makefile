# Apolo School MVP: macOS/Linux entry point (Windows keeps using start.ps1 / stop.ps1).
# Commands live in ./scripts. Run `make` or `make help` to list them.
.DEFAULT_GOAL := help
.PHONY: help tunnel start stop restart reset logs ps test benchmark benchmark-mock

help: ## Show this help
	@awk 'BEGIN {FS = ":.*## "} /^[a-z-]+:.*## / {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

tunnel: ## Expose agent-api (8000) via ngrok for the WhatsApp webhook
	@command -v ngrok >/dev/null || { echo "Install ngrok first: https://ngrok.com/download"; exit 1; }
	@echo "Meta callback URL: https://<ngrok-host>/webhooks/whatsapp"
	ngrok http 8000

start: ## Ask which school to run, then start everything for it (SCHOOL=<id> skips the menu, NO_BROWSER=1 skips tabs)
	@scripts/start.sh

stop: ## Stop every school; data is kept
	@scripts/stop.sh

restart: stop start ## Stop, then start

reset: ## DELETE the current school's data (ALL=1 for every school; asks for confirmation)
	@scripts/reset.sh

logs: ## Follow service logs (SERVICE=agent-api to pick one)
	@. scripts/common.sh && docker compose logs -f --tail=100 $(SERVICE)

ps: ## Show container status
	@. scripts/common.sh && docker compose ps

test: ## Run all pytest suites in .venv
	@scripts/test.sh

benchmark: ## Run the benchmark against the real model (needs Ollama)
	@scripts/benchmark.sh

benchmark-mock: ## Run the benchmark with canned replies (guardrails only, no Ollama)
	@scripts/benchmark.sh --mock-llm
