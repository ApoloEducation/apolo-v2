# Conectando o WhatsApp real (WhatsApp Cloud API da Meta)

O agente já funciona com o Mini-WhatsApp. Este guia liga o **mesmo agente** a um número de WhatsApp de verdade: cada telefone cadastrado vira um responsável (g01…g12), com a mesma lógica de dados, encaminhamento humano e Chatwoot.

```text
Pai/mãe (WhatsApp) -> Meta Cloud API -> https://SEU-TUNEL/webhooks/whatsapp -> Agent API (:8000)
                                                                                 |-> Chatwoot / escola / Ollama
Pai/mãe <- Meta Cloud API <- Agent API  (respostas do agente e do atendente humano)
```

## 1. Criar a conta Meta (uma vez, ~20 min)

1. Crie/entre numa conta em <https://developers.facebook.com> e conclua o cadastro de desenvolvedor.
2. **My Apps → Create App** → tipo **Business** (em alguns fluxos: "Other" → "Business").
3. No painel do app, adicione o produto **WhatsApp** → **API Setup** (Configuração da API). Se pedir, crie/associe um *Business Portfolio* (conta Meta Business).
4. Na página **API Setup** você ganha de graça um **número de teste** e vê:
   - **Phone number ID** → `WHATSAPP_PHONE_NUMBER_ID`
   - **Temporary access token** (vale 24 h) → `WHATSAPP_ACCESS_TOKEN`
5. Em **"To"**, adicione (**Manage phone number list**) os celulares que vão conversar com o bot e confirme o código recebido no WhatsApp. O número de teste só envia para até 5 números cadastrados.

## 2. Token permanente (recomendado antes da demo)

O token temporário expira em 24 h. Para um token que não expira:

1. <https://business.facebook.com/settings> → **Users → System users → Add** (função Admin).
2. **Add assets** → seu app, com controle total.
3. **Generate new token** → escolha o app → permissões `whatsapp_business_messaging` e `whatsapp_business_management` → copie para `WHATSAPP_ACCESS_TOKEN`.

## 3. Preencher o `.env` (único arquivo de configuração)

Na raiz do projeto, edite `.env` (o `.env.example` mostra todas as chaves):

```env
WHATSAPP_ACCESS_TOKEN=EAAG...           # passo 1/2
WHATSAPP_PHONE_NUMBER_ID=1234567890     # passo 1
WHATSAPP_VERIFY_TOKEN=apolo-verify-token  # qualquer texto; use o mesmo no passo 5
WHATSAPP_APP_SECRET=                    # App settings → Basic → App secret (valida a assinatura; recomendado)
WHATSAPP_GUARDIAN_PHONES=5511999990001=g01,5511988880002=g02
```

`WHATSAPP_GUARDIAN_PHONES` liga cada telefone (só dígitos, com país+DDD, ex.: `55` + `11` + número) a um responsável do demo (`g01`…`g12`; veja nomes e filhos no Mini-WhatsApp ou no banco em <http://localhost:8081>). Números fora dessa lista recebem uma mensagem pedindo para contatar a secretaria e **nunca** acessam dados. Números brasileiros com ou sem o 9º dígito são reconhecidos.

Depois reinicie o agente: `docker compose up -d agent-api`.

## 4. Expor o agente na internet (túnel)

A Meta precisa de uma URL **HTTPS pública**. Em desenvolvimento use um túnel para a porta 8000:

```powershell
# opção A – Cloudflare (sem conta): https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/
cloudflared tunnel --url http://localhost:8000
# opção B – ngrok (conta gratuita): https://ngrok.com/download
ngrok http 8000
```

Anote a URL `https://xxxx.trycloudflare.com` (ou `ngrok-free.app`). Ela muda a cada execução do túnel — atualize o passo 5 se mudar.

## 5. Configurar o webhook na Meta

No app: **WhatsApp → Configuration → Webhook → Edit**:

- **Callback URL:** `https://SEU-TUNEL/webhooks/whatsapp`
- **Verify token:** o mesmo valor de `WHATSAPP_VERIFY_TOKEN`

Clique em **Verify and save** e depois, em *Webhook fields*, assine (**Subscribe**) o campo **`messages`**.

## 6. Testar

1. Do celular cadastrado, mande uma mensagem para o **número de teste** (aparece em API Setup; ou clique no botão de mensagem de teste primeiro). Se a conversa ainda não existir, envie "oi" a partir do botão *Send message* de API Setup para abrir a janela.
2. Pergunte "Qual é a nota de Matemática?". A resposta chega no WhatsApp e a conversa aparece no Chatwoot (<http://localhost:3000>).
3. Pergunte algo sensível (ex.: mensalidade). O agente avisa o encaminhamento; responda pelo Chatwoot e a resposta chega no WhatsApp com o nome do atendente. Ao **resolver** a conversa, o bot volta ao modo automático.

## Mini-WhatsApp e WhatsApp juntos

O histórico é por responsável, então os dois canais compartilham a mesma conversa: se você usa o Mini-WhatsApp como `g01` e `g01` tem telefone cadastrado, as respostas também vão ao WhatsApp. Para testar só o Mini-WhatsApp, deixe `WHATSAPP_ACCESS_TOKEN` vazio.

## Problemas comuns

| Sintoma | Causa provável |
| --- | --- |
| "Verify and save" falha | Túnel fora do ar, URL sem `/webhooks/whatsapp`, ou verify token diferente. |
| Mensagem enviada não chega ao bot | Campo `messages` não assinado; app em modo desenvolvimento e número não adicionado; veja `docker compose logs agent-api`. |
| Log `fora do cadastro` | Telefone não está em `WHATSAPP_GUARDIAN_PHONES`. |
| Log `envio WhatsApp falhou (401/190)` | Token expirado (temporário dura 24 h) — gere o permanente. |
| `(131030)` Recipient not in allowed list | Adicione o celular na lista "To" do API Setup. |
| `(131047)` / fora da janela de 24 h | Mensagens livres só dentro de 24 h após a última mensagem do pai. Fora disso é preciso um *template* aprovado. |
| 403 no webhook | `WHATSAPP_APP_SECRET` incorreto. |

## Indo para produção (resumo)

- Número próprio: em API Setup → *Add phone number*, verifique um número que **não** esteja em uso no app WhatsApp. Exige verificação do Business Portfolio para liberar volume.
- Mensagens iniciadas pela escola exigem *templates* aprovados pela Meta (a janela de 24 h só abre quando o pai escreve primeiro).
- Hospede o agente com HTTPS fixo (sem túnel) e mantenha `WHATSAPP_APP_SECRET` definido.
- A Meta cobra por conversa/mensagem conforme a categoria; consulte os preços atuais em <https://developers.facebook.com/docs/whatsapp/pricing>.

Referências: [Get Started](https://developers.facebook.com/documentation/business-messaging/whatsapp/get-started) · [Webhooks](https://developers.facebook.com/docs/graph-api/webhooks) · [Enviar mensagens](https://developers.facebook.com/docs/whatsapp/cloud-api/guides/send-messages)
