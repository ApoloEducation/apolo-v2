# Apolo School MVP

Ambiente local de demonstração para avaliar o Chatwoot e um atendimento escolar automatizado. Todos os nomes e dados são fictícios.

## Início rápido

Pré-requisitos: [Docker Desktop](https://www.docker.com/products/docker-desktop/) em execução, Ollama instalado e acesso à internet no primeiro uso para baixar `qwen2.5:3b`.

**Windows** (PowerShell, na raiz do projeto):

```powershell
.\start.ps1
```

**macOS / Linux** (terminal, na raiz do projeto; o Makefile chama os scripts de `scripts/`):

```bash
make start        # pergunta a escola e sobe tudo só para ela; SCHOOL=aurora make start pula o menu; NO_BROWSER=1 não abre abas
make stop         # interrompe, mantendo os dados
make help         # lista todos os comandos (restart, reset, logs, ps, test, benchmark, benchmark-mock)
```

O script cria `.env` a partir de `.env.example`, inicia o Ollama se necessário, obtém o modelo, prepara o banco do Chatwoot, sobe os serviços e cria a conta demo. No primeiro uso, as imagens e o modelo podem demorar alguns minutos para baixar.

| Serviço | Endereço |
| --- | --- |
| Mini-WhatsApp | http://localhost:5173 |
| Chatwoot | http://localhost:3000 |
| Agent API (diagnóstico) | http://localhost:8000/health |
| Painel da escola: avisos, frequência, notas e banco de dados (requer `ADMIN_TOKEN` no `.env`) | http://localhost:8001/admin |

Credenciais locais do Chatwoot:

- Administrador: `admin@escola.local` / `ApoloLocal2026!`
- Atendente: `atendente@escola.local` / `ApoloLocal2026!`

Para interromper, execute `./stop.ps1` (Windows) ou `make stop` (macOS/Linux). Os volumes Docker são preservados. Para recomeçar completamente, use `docker compose down -v` ou `make reset` (isso remove todos os dados locais).

## Como testar o fluxo

1. Abra o Mini-WhatsApp e selecione qualquer responsável fictício da escola escolhida no `make start`.
2. Pergunte, por exemplo, “Qual é a nota de Matemática?” ou “Quando é a reunião?”. A resposta deve ser criada pelo agente e também aparecer no Chatwoot.
3. Pergunte sobre saúde, disciplina, outro aluno ou algo sem dados. O agente avisará sobre o encaminhamento e adicionará a etiqueta `escalado-humano` no Chatwoot.
4. Entre no Chatwoot com a conta de atendente, responda a conversa aberta e confirme a mensagem no Mini-WhatsApp. Ao resolver a conversa no Chatwoot, a próxima pergunta volta ao modo automático.

## Arquitetura

```text
Messenger (React) -> Agent API (FastAPI) -> Chatwoot API
                                      <- webhook <- Chatwoot / atendente
Agent API -> School Data (FastAPI + SQLite) -> Ollama qwen2.5:3b
```

`school-data` só oferece contexto já filtrado pelo ID do responsável. O LLM não recebe SQL nem acesso ao banco: recebe exclusivamente os dados dos filhos autorizados. O Chatwoot guarda as conversas operacionais; o pequeno banco do agente apenas associa sessões locais a conversas do Chatwoot e mantém o modo automático/humano.

## Várias escolas

No macOS/Linux, cada escola roda em um ambiente totalmente separado: um projeto Docker Compose próprio (`apolo-school-mvp-<id>`), com seus próprios volumes de banco da escola, Chatwoot (Postgres, Redis, arquivos) e conversas. Só uma escola roda por vez, pois todas usam as mesmas portas; `make start` interrompe a anterior sem apagar nada. Na primeira vez, cada escola prepara seu próprio Chatwoot, o que leva alguns minutos. `make reset` apaga apenas a escola atual; `make reset ALL=1` apaga todas. No Windows, `start.ps1` continua mostrando todas as escolas em um único ambiente.

Cada escola tem seu próprio arquivo SQLite (`$SCHOOL_DB_DIR/<school_id>.sqlite3`), criado e populado na inicialização a partir do registro `SCHOOLS` em `apps/school-data/app/seed.py`. Para adicionar uma escola, inclua uma entrada nesse registro. Todas as rotas são `/schools/{school_id}/...`; `school_id` é validado (`^[a-z0-9][a-z0-9-]{1,31}$`) e escolas desconhecidas retornam 404. Conversas e mensagens do agente são identificadas por `(school_id, guardian_id)`; bancos antigos são migrados para `horizonte`.

## Guardrails

`apps/agent-api/app/guardrails.py` aplica três camadas, sempre com falha fechada (qualquer violação encaminha para um humano): **entrada** (temas sensíveis, injeção de prompt, outros alunos por nome completo/primeiro nome, outras escolas, tamanho), **contexto** (o modelo só recebe campos necessários, sem IDs) e **saída** (sem nomes de outros alunos, URLs ou código, e todo número da resposta precisa existir nos dados autorizados).

## Benchmark

```powershell
python benchmarks/run_benchmark.py --mock-llm   # só guardrails, sem Ollama
python benchmarks/run_benchmark.py              # modelo real (Ollama + qwen2.5:3b)
```

Mede acurácia das respostas, taxa de escalonamento indevido, recall de escalonamento, vazamentos (deve ser 0; o script sai com código 1 caso contrário), erros e latência p50/p95. Resultados em `benchmarks/results/` (ignorado pelo git). Casos extras podem ser adicionados em `benchmarks/cases/guardrails.jsonl`.

## Estrutura

- `apps/messenger`: interface web de demonstração.
- `apps/agent-api`: ponte Chatwoot, decisão de encaminhamento, WebSocket e Ollama.
- `apps/school-data`: base SQLite sem dados reais e API de leitura restrita.
- `infra/chatwoot`: bootstrap idempotente da conta, Inbox API, equipe e etiqueta.

## Testes

Com Python 3.12 e dependências instaladas para cada serviço:

No macOS/Linux, `make test` cria o `.venv` e roda tudo. No PowerShell:

```powershell
Push-Location apps/school-data; python -m pytest tests; Pop-Location
Push-Location apps/agent-api; python -m pytest tests; Pop-Location
python -m pytest benchmarks
```

Também valide manualmente o fluxo completo listado acima após `./start.ps1`. As APIs de dados não expõem endpoints de SQL genérico ou de alunos fora do perfil autenticado.

## Solução de problemas

- **Docker não encontrado/em execução:** instale ou abra o Docker Desktop; o script interrompe com uma mensagem acionável.
- **Ollama não responde:** rode `ollama serve` ou deixe o `start.ps1` iniciá-lo; confirme `ollama list` e `ollama pull qwen2.5:3b`.
- **Serviço não inicia:** consulte `docker compose logs agent-api`, `docker compose logs chatwoot` ou `docker compose logs messenger`.
- **Reset do demo:** `docker compose down -v`, e rode o script novamente.
