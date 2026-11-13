# Apolo School MVP

Ambiente local de demonstração para avaliar o Chatwoot e um atendimento escolar automatizado. Todos os nomes e dados são fictícios.

Escolas disponíveis (cada uma com seus próprios alunos, responsáveis, avisos e Chatwoot):

| ID | Escola | Turmas | Alunos |
| --- | --- | --- | --- |
| `horizonte` | Escola Horizonte | 6º A, 7º B, 8º C | 12 |
| `aurora` | Colégio Aurora | 5º A, 9º B | 6 |
| `vale-verde` | Escola Vale Verde | 7º A | 4 |

## Início rápido

Pré-requisitos: [Docker Desktop](https://www.docker.com/products/docker-desktop/) em execução, e um modelo de linguagem: Ollama instalado (padrão; o modelo vem de `OLLAMA_MODEL`, hoje `qwen2.5:14b`, baixado no primeiro uso) ou uma chave do [OpenRouter](https://openrouter.ai) (veja “Usando o OpenRouter”).

Em macOS ou Linux (terminal, na raiz do projeto; o Makefile chama os scripts de `scripts/`):

```bash
make start        # pergunta a escola e sobe tudo só para ela; SCHOOL=aurora make start pula o menu; NO_BROWSER=1 não abre abas
make start LLM=openrouter   # usa o OpenRouter em vez do Ollama local (veja abaixo)
make stop         # interrompe, mantendo os dados
make help         # lista todos os comandos (restart, reset, logs, ps, test, benchmark, benchmark-mock, tunnel)
```

O script cria `.env` a partir de `.env.example`, inicia o Ollama se necessário e obtém o modelo (ou valida as variáveis do OpenRouter), prepara o banco do Chatwoot, sobe os serviços e cria a conta demo. No primeiro uso, as imagens e o modelo podem demorar alguns minutos para baixar.

| Serviço | Endereço |
| --- | --- |
| Mini-WhatsApp | http://localhost:5173 |
| Chatwoot | http://localhost:3000 |
| Agent API (diagnóstico) | http://localhost:8000/health |
| Painel da escola (requer `ADMIN_TOKEN` no `.env`; só acessível da própria máquina) | http://localhost:8001/admin |

O painel tem quatro páginas: **avisos** (`/admin/avisos`), **frequência** (chamada diária, `/admin/frequencia`), **notas** (`/admin/notas`) e **banco** (edição das tabelas, `/admin/banco`). Ao abrir, informe o valor de `ADMIN_TOKEN` na tela de entrada. O painel serve a escola escolhida no `make start` (o seletor de escola do topo só aparece quando há mais de uma). A frequência diária ajusta os totais que o agente lê; as demais alterações também valem para as respostas do agente.

Credenciais locais do Chatwoot:

- Administrador: `admin@escola.local` / `ApoloLocal2026!`
- Atendente: `atendente@escola.local` / `ApoloLocal2026!`

Para interromper, execute `make stop` (ou `make restart` para parar e subir de novo; ele pergunta a escola novamente). Os volumes Docker são preservados. Para recomeçar completamente, use `docker compose down -v` ou `make reset` (isso remove todos os dados locais).

## Como testar o fluxo

1. Abra o Mini-WhatsApp e selecione qualquer responsável fictício da escola escolhida no `make start`.
2. Pergunte, por exemplo, “Qual é a nota de Matemática?” ou “Quando é a reunião?”. A resposta deve ser criada pelo agente e também aparecer no Chatwoot.
3. Pergunte sobre saúde, disciplina, outro aluno ou algo sem dados. O agente avisará sobre o encaminhamento e adicionará a etiqueta `escalado-humano` no Chatwoot.
4. Entre no Chatwoot com a conta de atendente, responda a conversa aberta e confirme a mensagem no Mini-WhatsApp. Ao resolver a conversa no Chatwoot, a próxima pergunta volta ao modo automático.

## Arquitetura

```text
Messenger (React) -> Agent API (FastAPI) -> Chatwoot API
                                      <- webhook <- Chatwoot / atendente
Agent API -> School Data (FastAPI + SQLite) -> LLM (Ollama local ou OpenRouter)
WhatsApp (Meta Cloud API) <-> Agent API  (opcional, via make tunnel)
```

`school-data` só oferece dados filtrados pelo ID do responsável. O agente consulta o banco em um laço de SQL: o modelo propõe `SELECT`s somente leitura, que o `school-data` valida e restringe ao escopo do responsável (consultas inválidas são rejeitadas), e a resposta só pode usar números vindos desses resultados. O modelo nunca acessa o banco diretamente. O Chatwoot guarda as conversas operacionais; o pequeno banco do agente apenas associa sessões locais a conversas do Chatwoot e mantém o modo automático/humano.

## Configuração (`.env`)

O `.env` é criado a partir de `.env.example` no primeiro `make start`. Variáveis principais:

| Variável | Para que serve |
| --- | --- |
| `OLLAMA_MODEL` | Modelo local do Ollama (padrão no exemplo: `qwen2.5:14b`) |
| `OPENROUTER_MODEL`, `OPEN_ROUTER_API_KEY` | Modelo e chave usados com `make start LLM=openrouter` |
| `OLLAMA_CLASSIFIER_MODEL`, `OPENROUTER_CLASSIFIER_MODEL` | Modelo de decisão que classifica o tema de cada mensagem (padrão: `d3:2b` e `typesafe/jev-1.13`) |
| `CLASSIFIER_BLOCK_THRESHOLD`, `CLASSIFIER_TIMEOUT` | Probabilidade somada dos temas sensíveis a partir da qual encaminha (0,30; menor = mais rígido) e tempo limite da chamada |
| `ADMIN_TOKEN` | Habilita o painel da escola; vazio = painel desativado |
| `CORS_ORIGINS` | Origens de navegador permitidas na Agent API |
| `META_API_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_VERIFY_TOKEN`, `WHATSAPP_APP_SECRET`, `WHATSAPP_ALLOWED_NUMBERS` | Integração com o WhatsApp (veja o final deste arquivo) |
| `CHATWOOT_*` | Segredos e contas locais do Chatwoot |
| `APOLO_SCHOOL`, `COMPOSE_PROJECT_NAME` | Escritas pelo `make start` para lembrar a escola atual; não edite |

Modelos de raciocínio (ex.: qwen3) aceitam `OLLAMA_THINK=1|0` e o tempo limite de cada chamada é `OLLAMA_TIMEOUT` (90 s).

## Usando o OpenRouter

Por padrão o agente usa o Ollama local. Para usar um modelo hospedado no OpenRouter, defina no `.env`:

```env
OPENROUTER_MODEL=<id do modelo, ex.: provedor/modelo>
OPEN_ROUTER_API_KEY=<sua chave>
```

e inicie com:

```bash
make start LLM=openrouter
```

Nesse modo o `make start` não exige nem inicia o Ollama, e falha com uma mensagem clara se `OPENROUTER_MODEL` ou `OPEN_ROUTER_API_KEY` estiverem vazios. A escolha vale apenas para essa execução: sem `LLM=openrouter`, volta ao Ollama (`make restart` não repassa o parâmetro; use `make stop` e depois `make start LLM=openrouter`). As perguntas e dados autorizados são enviados ao OpenRouter, então use apenas dados fictícios. O benchmark usa o mesmo `LLM_PROVIDER` (do ambiente ou do `.env`): `LLM_PROVIDER=openrouter make benchmark`.

## Várias escolas

Cada escola roda em um ambiente totalmente separado: um projeto Docker Compose próprio (`apolo-school-mvp-<id>`), com seus próprios volumes de banco da escola, Chatwoot (Postgres, Redis, arquivos) e conversas. Só uma escola roda por vez, pois todas usam as mesmas portas; `make start` interrompe a anterior sem apagar nada. Na primeira vez, cada escola prepara seu próprio Chatwoot, o que leva alguns minutos. `make reset` apaga apenas a escola atual; `make reset ALL=1` apaga todas.

As três escolas acima vêm do registro; no Mini-WhatsApp há um seletor de escola e de responsável. Cada escola tem seu próprio arquivo SQLite (`$SCHOOL_DB_DIR/<school_id>.sqlite3`), criado e populado na inicialização a partir do registro `SCHOOLS` em `apps/school-data/app/seed.py`. Para adicionar uma escola, inclua uma entrada nesse registro. Todas as rotas são `/schools/{school_id}/...`; `school_id` é validado (`^[a-z0-9][a-z0-9-]{1,31}$`) e escolas desconhecidas retornam 404. Conversas e mensagens do agente são identificadas por `(school_id, guardian_id)`; bancos antigos são migrados para `horizonte`.

## Guardrails

Os prompts dos modelos ficam em `configs/`: `configs/agent.toml` (quem é o Apolo, tom, regras, mensagens válidas e inválidas) e `configs/classifier.toml` (os temas). `make prompt` mostra o prompt montado; depois de editar, `docker compose restart agent-api` aplica a mudança sem rebuild.

Cada mensagem passa primeiro por um classificador de tema (`apps/agent-api/app/classifier.py`, temas em `configs/classifier.toml`), um modelo de decisão (`d3:2b` no Ollama, `typesafe/jev-1.13` no OpenRouter) que escolhe um entre 16 temas. Temas sensíveis (saúde, disciplina, jurídico, financeiro, dados pessoais, outros alunos, outras turmas, outras escolas) encaminham para um humano, com a etiqueta `motivo-<tema>` no Chatwoot. Injeção de prompt e assuntos fora da escola recebem uma recusa educada, sem encaminhar. Cortesias (oi, obrigado) e perguntas escolares (notas, frequência, horários, avisos) são respondidas. Todo tema fica salvo por mensagem, e `GET /v1/themes?school=<id>&since=AAAA-MM-DD&until=AAAA-MM-DD` conta as mensagens por tema. `apps/agent-api/app/guardrails.py` aplica o limite de tamanho (600 caracteres) e, sempre com falha fechada (qualquer violação encaminha para um humano), as camadas de **contexto** (o modelo só recebe campos necessários, sem IDs) e **saída** (sem nomes de outros alunos, URLs ou código, e todo número da resposta precisa existir nos dados autorizados).

## Adicionando uma ferramenta ao agente

O agente (`apps/agent-api/app/agent.py`) é um `Agent` do [Pydantic AI](https://pydantic.dev/docs/ai/) com uma ferramenta, `run_sql`. Para adicionar outra: escreva uma função `@apolo.tool` (a docstring e os parâmetros tipados são o que o modelo lê; dados por conversa ficam em `ctx.deps`), coloque em `configs/agent.toml` qualquer texto que o modelo veja, e adicione um caso em `benchmarks/cases/`. Os guardrails continuam fora do agente: o classificador roda antes e `check_output` depois, sem nova tentativa. Nos testes, o modelo é um `FunctionModel` roteirizado (veja `Script` em `apps/agent-api/tests/test_agent.py`).

## Documentação adicional

- `docs/guardrails.md`: como as camadas de proteção funcionam e como estendê-las.
- `docs/benchmark.md` e `docs/benchmark-report.md`: método e resultados do benchmark (comparação de modelos e desenhos).

## Benchmark

```bash
python benchmarks/run_benchmark.py --mock-llm          # só guardrails, sem Ollama
python benchmarks/run_benchmark.py --mock-classifier   # modelo real, mas com o tema esperado de cada caso (isola o agente)
python benchmarks/run_benchmark.py              # modelo real (Ollama; OLLAMA_URL=http://localhost:11434, modelo em OLLAMA_MODEL)
```

Cada caso tem um comportamento esperado (responder com o fato certo, encaminhar a um humano ou recusar) e passa ou falha; a métrica principal é a taxa de casos aprovados, com vazamentos (deve ser 0; o script sai com código 1 caso contrário), encaminhamentos desnecessários, latência e custo ao lado. Cada execução completa entra em `benchmarks/history.jsonl` e reconstrói `benchmarks/report.html` (abra no navegador: ranking dos setups, áreas em que cada um falha e os casos que falharam). Os rastros completos (resposta, SQL, probabilidades do classificador) ficam em `benchmarks/results/` (ignorado pelo git). Opções úteis: `--runs 3` (repete para medir instabilidade), `--model` e `--classifier` (troca os modelos), `--classifier-only` (só o classificador, muito barato), `--filter REGEX` (alguns casos). Casos ficam em `benchmarks/cases/*.jsonl` (`guardrails.jsonl`, `conversations.jsonl`, `coverage.jsonl`). `make benchmark` e `make benchmark-mock` fazem o mesmo pelo Makefile.

## Estrutura

- `apps/messenger`: interface web de demonstração.
- `apps/agent-api`: ponte Chatwoot, guardrails, agente [Pydantic AI](https://pydantic.dev/docs/ai/) com a ferramenta `run_sql` (`app/agent.py`; modelo Ollama/OpenRouter em `app/llm.py`), WebSocket e webhook do WhatsApp.
- `apps/school-data`: bases SQLite por escola (dados fictícios), API de leitura restrita e painel `/admin`.
- `infra/chatwoot`: bootstrap idempotente da conta, Inbox API, equipe e etiqueta.
- `benchmarks` e `docs`: casos de benchmark, relatório, e detalhes de guardrails.

## Testes

Com Python 3.12 e dependências instaladas para cada serviço:

`make test` cria o `.venv` e roda tudo. Para rodar manualmente:

```bash
(cd apps/school-data && python -m pytest tests)
(cd apps/agent-api && python -m pytest tests)
python -m pytest benchmarks
```

Também valide manualmente o fluxo completo listado acima após `make start`. A API de dados só executa `SELECT`s somente leitura, restritos ao responsável autenticado.

## Solução de problemas

- **Docker não encontrado/em execução:** instale ou abra o Docker Desktop; o script interrompe com uma mensagem acionável.
- **Ollama não responde:** rode `ollama serve` ou deixe o `make start` iniciá-lo; confirme `ollama list` e `ollama pull <OLLAMA_MODEL>`. Sem Ollama, use `make start LLM=openrouter`.
- **OpenRouter falha:** confira `OPENROUTER_MODEL` e `OPEN_ROUTER_API_KEY` no `.env` e veja `docker compose logs agent-api`.
- **Serviço não inicia:** consulte `docker compose logs agent-api`, `docker compose logs chatwoot` ou `docker compose logs messenger`.
- **Reset do demo:** `docker compose down -v`, e rode o script novamente.

## WhatsApp real (Meta Cloud API, modo de teste)

1. No `.env`: `META_API_TOKEN` (token de acesso), `WHATSAPP_PHONE_NUMBER_ID` (o "Phone Number ID" do número de teste no painel da Meta, não o seu telefone), `WHATSAPP_VERIFY_TOKEN` (qualquer texto que você inventar; não é o token de acesso) e `WHATSAPP_ALLOWED_NUMBERS=<seu número com DDI>:<escola>:<responsável>` (ex.: `5511999999999:horizonte:g01`). Remetentes fora da lista são ignorados; números brasileiros valem com ou sem o 9 extra.
2. `make start` e, em outro terminal, `make tunnel` (requer [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/), `brew install cloudflared`; expõe a porta 8000 sem precisar de conta). O endereço `https://<host>.trycloudflare.com` aparece no terminal e muda a cada execução.
3. No painel da Meta (WhatsApp > Configuração > Configurar webhooks): Callback URL `https://<host>.trycloudflare.com/webhooks/whatsapp`, Verify token igual ao `WHATSAPP_VERIFY_TOKEN` do `.env`, e assine o campo `messages`. Adicione seu número como destinatário de teste. Ao reiniciar o túnel, atualize a Callback URL.
4. Envie uma mensagem do seu celular para o número de teste. A resposta do agente e as respostas de atendentes do Chatwoot chegam no WhatsApp.

Limitações: só texto; fora da janela de 24h a Meta exige templates (não implementado).
