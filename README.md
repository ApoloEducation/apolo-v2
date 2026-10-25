# Apolo School MVP

Ambiente local de demonstração para avaliar o Chatwoot e um atendimento escolar automatizado. Todos os nomes e dados são fictícios.

## Início rápido

Pré-requisitos: [Docker Desktop](https://www.docker.com/products/docker-desktop/) em execução, Ollama instalado e acesso à internet no primeiro uso para baixar `qwen2.5:3b`.

No PowerShell, na raiz do projeto:

```powershell
.\start.ps1
```

O script cria `.env` a partir de `.env.example`, inicia o Ollama se necessário, obtém o modelo, prepara o banco do Chatwoot, sobe os serviços e cria a conta demo. No primeiro uso, as imagens e o modelo podem demorar alguns minutos para baixar.

| Serviço | Endereço |
| --- | --- |
| Mini-WhatsApp | http://localhost:5173 |
| Chatwoot | http://localhost:3000 |
| Agent API (diagnóstico) | http://localhost:8000/health |

Credenciais locais do Chatwoot:

- Administrador: `admin@escola.local` / `ApoloLocal2026!`
- Atendente: `atendente@escola.local` / `ApoloLocal2026!`

Para apagar todas as mensagens (assistente, Chatwoot e telas abertas) sem derrubar nada, use `./clear.ps1` ou o botão "Limpar tudo" do messenger. O WhatsApp real não permite apagar o que já chegou no celular.

Para interromper, execute `./stop.ps1`. Os volumes Docker são preservados. Para recomeçar completamente, use `docker compose down -v` (isso remove todos os dados locais).

## Como testar o fluxo

1. Abra o Mini-WhatsApp e selecione qualquer responsável fictício.
2. Pergunte, por exemplo, “Qual é a nota de Matemática?” ou “Quando é a reunião?”. A resposta deve ser criada pelo agente e também aparecer no Chatwoot.
3. Pergunte sobre saúde, disciplina, outro aluno ou algo sem dados. O agente avisará sobre o encaminhamento e adicionará a etiqueta `escalado-humano` no Chatwoot.
4. Entre no Chatwoot com a conta de atendente, responda a conversa aberta e confirme a mensagem no Mini-WhatsApp. Ao resolver a conversa no Chatwoot, a próxima pergunta volta ao modo automático.

## WhatsApp real

Para trocar o Mini-WhatsApp por um número de WhatsApp de verdade (Cloud API da Meta), siga [docs/WHATSAPP.md](docs/WHATSAPP.md): basta preencher as chaves `WHATSAPP_*` no `.env`.

## Arquitetura

```text
Messenger (React) -> Agent API (FastAPI) -> Chatwoot API
                                      <- webhook <- Chatwoot / atendente
Agent API -> School Data (FastAPI + SQLite) -> Ollama qwen2.5:3b
```

`school-data` só oferece contexto já filtrado pelo ID do responsável. O LLM não recebe SQL nem acesso ao banco: recebe exclusivamente os dados dos filhos autorizados. O Chatwoot guarda as conversas operacionais; o pequeno banco do agente apenas associa sessões locais a conversas do Chatwoot e mantém o modo automático/humano.

## Estrutura

- `apps/messenger`: interface web de demonstração.
- `apps/agent-api`: ponte Chatwoot, decisão de encaminhamento, WebSocket e Ollama.
- `apps/school-data`: base SQLite sem dados reais e API de leitura restrita.
- `infra/chatwoot`: bootstrap idempotente da conta, Inbox API, equipe e etiqueta.

## Testes

Com Python 3.12 e dependências instaladas para cada serviço:

```powershell
Push-Location apps/school-data; python -m pytest tests; Pop-Location
Push-Location apps/agent-api; python -m pytest tests; Pop-Location
```

Também valide manualmente o fluxo completo listado acima após `./start.ps1`. As APIs de dados não expõem endpoints de SQL genérico ou de alunos fora do perfil autenticado.

## Solução de problemas

- **Docker não encontrado/em execução:** instale ou abra o Docker Desktop; o script interrompe com uma mensagem acionável.
- **Ollama não responde:** rode `ollama serve` ou deixe o `start.ps1` iniciá-lo; confirme `ollama list` e `ollama pull qwen2.5:3b`.
- **Serviço não inicia:** consulte `docker compose logs agent-api`, `docker compose logs chatwoot` ou `docker compose logs messenger`.
- **Reset do demo:** `docker compose down -v`, apague `infra/chatwoot/integration.json` caso exista e rode o script novamente.
