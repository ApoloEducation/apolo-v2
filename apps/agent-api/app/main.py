import asyncio
import json
import logging
import os
from collections import defaultdict

import httpx

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import whatsapp
from .agent import Escalate, answer
from .chatwoot import Chatwoot, ChatwootUnavailable
from .state import (claim_webhook_message, claim_whatsapp_message, conversation, guardian_for_conversation, history, initialize,
                    mark_local_delivery, save_conversation, save_message, was_locally_delivered)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("apolo.api")

app = FastAPI(title="Apolo Agent API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
clients: dict[str, set[WebSocket]] = defaultdict(set)
locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
chatwoot = Chatwoot()
RESOLVED_NOTICE = "Seu atendimento foi finalizado. Se precisar de algo mais, é só escrever por aqui!"
HANDOFF = "Vou encaminhar sua mensagem para nossa equipe de atendimento. Um atendente responderá por aqui assim que possível."


@app.exception_handler(Exception)
async def unhandled(_request: Request, exc: Exception):
    log.exception("erro não tratado")
    if isinstance(exc, httpx.HTTPError):
        return JSONResponse({"detail": "Serviço temporariamente indisponível. Tente novamente em instantes."}, status_code=503)
    return JSONResponse({"detail": "Erro interno. Tente novamente em instantes."}, status_code=500)


class MessageIn(BaseModel):
    guardian_id: str = Field(pattern=r"^g\d{2}$")
    guardian_name: str
    content: str = Field(min_length=1, max_length=2000)


@app.on_event("startup")
def startup():
    initialize()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/v1/guardians")
async def guardians():
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(f"{os.getenv('SCHOOL_DATA_URL', 'http://school-data:8000')}/guardians")
        response.raise_for_status()
        return response.json()


@app.get("/v1/history/{guardian_id}")
def get_history(guardian_id: str):
    return history(guardian_id)


async def broadcast(guardian_id: str, item: dict):
    await whatsapp.deliver(guardian_id, item)
    stale = []
    for socket in clients[guardian_id]:
        try:
            await socket.send_json(item)
        except Exception:
            stale.append(socket)
    for socket in stale:
        clients[guardian_id].discard(socket)


@app.post("/v1/messages")
async def create_message(message: MessageIn):
    async with locks[message.guardian_id]:
        return await handle_message(message)


async def handle_message(message: MessageIn):
    gid = message.guardian_id
    current = conversation(gid)
    save_message(gid, "guardian", message.content)
    await broadcast(gid, {"sender": "guardian", "content": message.content})
    try:
        contact_id, conversation_id = await chatwoot.ensure_contact_and_conversation(gid, message.guardian_name, current)
        mode = current["mode"] if current else "auto"
        save_conversation(gid, conversation_id, contact_id, mode)
        await chatwoot.message(conversation_id, message.content, "incoming")
        if mode == "human":
            return {"status": "queued"}
        try:
            reply = await answer(gid, message.content, history(gid)[:-1])
            escalation = None
        except Escalate as exc:
            escalation = exc
        except Exception as exc:  # noqa: BLE001 - never leave the parent without a response
            log.exception("falha ao gerar resposta; escalando")
            escalation = Escalate(f"erro: {type(exc).__name__}", question=message.content)
        if escalation:
            log.info("handoff gid=%s categoria=%s motivo=%s", gid, escalation.category, escalation.reason)
            # Human mode is saved first so a later failure can never leave the bot answering during a handoff.
            save_conversation(gid, conversation_id, contact_id, "human")
            reply = HANDOFF
        outgoing = await chatwoot.message(conversation_id, reply, "outgoing", bot=True)
        mark_local_delivery(outgoing.get("id"))
        save_message(gid, "agent", reply)
        await broadcast(gid, {"sender": "agent", "content": reply})
        if escalation:
            await chatwoot.escalate(conversation_id, escalation.category, escalation.reason, message.content)
        return {"status": "handoff" if escalation else "answered", "reply": reply}
    except ChatwootUnavailable as exc:
        raise HTTPException(503, str(exc))


@app.post("/webhooks/chatwoot")
async def chatwoot_webhook(payload: dict):
    event = payload.get("event")
    conversation_payload = payload.get("conversation") or {}
    # conversation_* events carry the conversation itself at the top level; message_* events nest it.
    conversation_id = conversation_payload.get("id") or payload.get("conversation_id")
    if not conversation_id and str(payload.get("event", "")).startswith("conversation_"):
        conversation_id = payload.get("id")
    status = payload.get("status") or conversation_payload.get("status")
    log.info("webhook event=%s conversation=%s message=%s", event, conversation_id, payload.get("id"))
    if event == "conversation_status_changed" and status == "resolved":
        guardian_id = guardian_for_conversation(conversation_id)
        if guardian_id:
            async with locks[guardian_id]:
                was_human = (conversation(guardian_id) or {}).get("mode") == "human"
                save_conversation(guardian_id, conversation_id, None, "auto")
                if was_human:
                    save_message(guardian_id, "agent", RESOLVED_NOTICE)
                    await broadcast(guardian_id, {"sender": "agent", "content": RESOLVED_NOTICE})
    if event == "message_created" and payload.get("message_type") == "outgoing" and not payload.get("private"):
        attributes = payload.get("content_attributes") or {}
        guardian_id = guardian_for_conversation(conversation_id)
        # Messages posted by the bot itself come back through this webhook, sometimes before we could register them.
        if not guardian_id or attributes.get("apolo_bot") or was_locally_delivered(payload.get("id")):
            return {"ok": True}
        if not claim_webhook_message(payload.get("id")):
            return {"ok": True}
        name = ((payload.get("sender") or {}).get("name") or "Atendente").strip()
        content = f"*{name}:* {payload.get('content') or ''}"
        save_message(guardian_id, "human", content)
        await broadcast(guardian_id, {"sender": "human", "content": content})
    return {"ok": True}


@app.get("/webhooks/whatsapp")
def whatsapp_verify(request: Request):
    """Meta's one-time handshake when the callback URL is saved in the app dashboard."""
    params = request.query_params
    token = os.getenv("WHATSAPP_VERIFY_TOKEN")
    if token and params.get("hub.mode") == "subscribe" and params.get("hub.verify_token") == token:
        return PlainTextResponse(params.get("hub.challenge", ""))
    raise HTTPException(403, "verify token inválido")


@app.post("/webhooks/whatsapp")
async def whatsapp_webhook(request: Request, background: BackgroundTasks):
    body = await request.body()
    if not whatsapp.valid_signature(body, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(403, "assinatura inválida")
    try:
        payload = json.loads(body or b"{}")
    except ValueError:
        raise HTTPException(400, "JSON inválido")
    # Answer Meta immediately (it retries slow webhooks); the LLM runs in the background.
    for incoming in whatsapp.parse_incoming(payload):
        if claim_whatsapp_message(incoming["id"]):
            background.add_task(process_whatsapp_message, incoming)
    return {"ok": True}


async def process_whatsapp_message(incoming: dict):
    phone = incoming["phone"]
    try:
        guardian_id = whatsapp.guardian_for_phone(phone)
        if not guardian_id:
            log.warning("whatsapp: número %s fora do cadastro (WHATSAPP_GUARDIAN_PHONES)", phone)
            await whatsapp.send_text(phone, whatsapp.UNKNOWN_NUMBER)
            return
        if incoming["type"] != "text" or not incoming["text"].strip():
            await whatsapp.send_text(phone, whatsapp.UNSUPPORTED)
            return
        name = await guardian_name(guardian_id) or incoming.get("name") or "Responsável"
        message = MessageIn(guardian_id=guardian_id, guardian_name=name, content=incoming["text"].strip()[:2000])
        async with locks[guardian_id]:
            await handle_message(message)
    except Exception:  # noqa: BLE001 - a background task has nobody to report to but the log
        log.exception("falha ao processar mensagem WhatsApp de %s", phone)


async def guardian_name(guardian_id: str) -> str | None:
    try:
        return next((g["name"] for g in await guardians() if g["id"] == guardian_id), None)
    except Exception:  # noqa: BLE001
        return None


@app.websocket("/ws/{guardian_id}")
async def websocket_endpoint(websocket: WebSocket, guardian_id: str):
    await websocket.accept()
    clients[guardian_id].add(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        clients[guardian_id].discard(websocket)
