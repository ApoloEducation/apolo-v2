import hmac
import json
import logging
import os
from collections import defaultdict

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, Path, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from . import whatsapp
from .agent import Answer, Escalate, answer
from .chatwoot import Chatwoot, ChatwootUnavailable
from .config import load_agent, load_classifier
from .state import (claim_wamid, consume_local_delivery, conversation, conversation_by_chatwoot_id, expect_local_delivery, history, initialize, mark_local_delivery,
                    save_conversation, save_message, set_message_theme, theme_counts, was_locally_delivered)

app = FastAPI(title="Apolo Agent API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",") if o.strip()],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
log = logging.getLogger("agent-api")
clients: dict[str, set[WebSocket]] = defaultdict(set)
chatwoot = Chatwoot()
DECLINE_REPLY = "Posso ajudar com assuntos escolares dos seus filhos, como notas, frequência, horários e avisos. Como posso ajudar?"
HANDOFF = "Vou encaminhar sua mensagem para nossa equipe de atendimento. Um atendente responderá por aqui assim que possível."


SCHOOL_PATTERN = r"^[a-z0-9][a-z0-9-]{1,31}$"
GUARDIAN_PATTERN = r"^g\d{2}$"


class MessageIn(BaseModel):
    school_id: str = Field(default="horizonte", pattern=SCHOOL_PATTERN)
    guardian_id: str = Field(pattern=GUARDIAN_PATTERN)
    guardian_name: str = Field(max_length=120)
    content: str = Field(min_length=1, max_length=2000)


@app.on_event("startup")
def startup():
    initialize()
    for name, loaded in (("agent", load_agent()), ("classifier", load_classifier())):
        log.info("prompt %s: %s (version %s)", name, loaded["_path"], loaded.get("version", "?"))


@app.get("/health")
def health():
    return {"status": "ok"}


async def school_data(path: str):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(f"{os.getenv('SCHOOL_DATA_URL')}{path}")
    except httpx.HTTPError:
        raise HTTPException(503, "Dados da escola indisponíveis")
    if response.status_code == 404:
        raise HTTPException(404, "Escola não encontrada")
    response.raise_for_status()
    return response.json()


@app.get("/v1/schools")
async def schools():
    return await school_data("/schools")


@app.get("/v1/schools/{school_id}/guardians")
async def guardians(school_id: str = Path(pattern=SCHOOL_PATTERN)):
    return await school_data(f"/schools/{school_id}/guardians")


@app.get("/v1/history/{school_id}/{guardian_id}")
def get_history(school_id: str = Path(pattern=SCHOOL_PATTERN), guardian_id: str = Path(pattern=GUARDIAN_PATTERN)):
    return history(school_id, guardian_id)


async def broadcast(key: str, item: dict):
    stale = []
    for socket in clients[key]:
        try:
            await socket.send_json(item)
        except Exception:
            stale.append(socket)
    for socket in stale:
        clients[key].discard(socket)


def key(school_id: str, guardian_id: str) -> str:
    return f"{school_id}/{guardian_id}"


async def handle_incoming(school: str, guardian: str, name: str | None, content: str) -> dict:
    """Channel-agnostic: store, mirror to Chatwoot, answer (or escalate). Used by the web UI and WhatsApp."""
    guardians = {g["id"]: g for g in await school_data(f"/schools/{school}/guardians")}
    if guardian not in guardians:
        raise HTTPException(404, "Responsável não encontrado")
    name = name or guardians[guardian].get("name") or guardian
    current = conversation(school, guardian)
    previous = history(school, guardian)  # before saving this message: it is passed to the model separately
    message_id = save_message(school, guardian, "guardian", content)
    await broadcast(key(school, guardian), {"sender": "guardian", "content": content})
    try:
        contact_id, conversation_id = await chatwoot.ensure_contact_and_conversation(school, guardian, name, current)
        mode = current["mode"] if current else "auto"
        save_conversation(school, guardian, conversation_id, contact_id, mode)
        await chatwoot.message(conversation_id, content, "incoming")
        if mode == "human":
            set_message_theme(message_id, "atendimento-humano", "queued")
            return {"status": "queued"}
        status = "answered"
        try:
            result: Answer = await answer(school, guardian, content, previous)
            reply = DECLINE_REPLY if result.declined else result.text
            status = "declined" if result.declined else "answered"
            set_message_theme(message_id, result.theme, status)
        except Escalate as exc:
            reply, status = HANDOFF, "answered"
            set_message_theme(message_id, exc.theme, "escalated")
            await chatwoot.escalate(conversation_id, exc.theme)
            save_conversation(school, guardian, conversation_id, contact_id, "human")
        expect_local_delivery(conversation_id, reply)
        outgoing = await chatwoot.message(conversation_id, reply, "outgoing")
        mark_local_delivery(outgoing.get("id"))
        save_message(school, guardian, "agent", reply)
        await broadcast(key(school, guardian), {"sender": "agent", "content": reply})
        return {"status": status, "reply": reply}
    except ChatwootUnavailable as exc:
        raise HTTPException(503, str(exc))


@app.get("/v1/themes")
def themes(school: str = Query(pattern=SCHOOL_PATTERN), since: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
           until: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")):
    """Guardian messages per theme and outcome (for monthly reviews). `since` inclusive, `until` exclusive, YYYY-MM-DD."""
    return theme_counts(school, since, until)


@app.post("/v1/messages")
async def create_message(message: MessageIn):
    return await handle_incoming(message.school_id, message.guardian_id, message.guardian_name, message.content)


@app.get("/webhooks/whatsapp")
def whatsapp_verify(request: Request):
    q = request.query_params
    expected = os.getenv("WHATSAPP_VERIFY_TOKEN")
    if expected and q.get("hub.mode") == "subscribe" and hmac.compare_digest(q.get("hub.verify_token", ""), expected):
        return PlainTextResponse(q.get("hub.challenge", ""))
    raise HTTPException(403, "Verification failed")


async def process_whatsapp_message(msg: dict):
    sender = msg.get("from", "")
    target = whatsapp.allowed_numbers().get(sender)
    if not target:
        log.info("Ignoring WhatsApp message from unmapped number")
        return
    school, guardian = target
    try:
        if msg.get("type") != "text":
            await whatsapp.send_text(sender, "Por enquanto só consigo ler mensagens de texto.")
            return
        text = (msg.get("text", {}).get("body") or "").strip()[:2000]
        if not text:
            return
        result = await handle_incoming(school, guardian, None, text)
        if result.get("reply"):
            await whatsapp.send_text(sender, result["reply"])
    except Exception:
        log.exception("Failed to process WhatsApp message")
        await whatsapp.send_text(sender, "Desculpe, tive um problema ao processar sua mensagem. Tente novamente em instantes.")


@app.post("/webhooks/whatsapp")
async def whatsapp_webhook(request: Request, background: BackgroundTasks):
    body = await request.body()
    if not whatsapp.valid_signature(body, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(403, "Invalid signature")
    try:
        payload = json.loads(body)
    except ValueError:
        raise HTTPException(400, "Invalid JSON")
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            for msg in (change.get("value") or {}).get("messages", []):
                if msg.get("id") and claim_wamid(msg["id"]):
                    background.add_task(process_whatsapp_message, msg)
    return {"ok": True}


@app.post("/webhooks/chatwoot")
async def chatwoot_webhook(payload: dict):
    event = payload.get("event")
    conversation_payload = payload.get("conversation", {})
    conversation_id = conversation_payload.get("id") or payload.get("conversation_id")
    if event == "conversation_status_changed":
        # Chatwoot sends the conversation itself as the payload for this event, so the id is top-level.
        conversation_id = conversation_id or payload.get("id")
    status = payload.get("status") or conversation_payload.get("status")
    row = conversation_by_chatwoot_id(conversation_id)
    if row and event == "conversation_status_changed" and status == "resolved":
        save_conversation(row["school_id"], row["guardian_id"], conversation_id, None, "auto")
    if row and event == "message_created" and payload.get("message_type") == "outgoing" and not payload.get("private"):
        content = payload.get("content") or ""
        # Skip echoes of our own replies (matched by id, or by content when the webhook beat our POST response).
        if not was_locally_delivered(payload.get("id")) and not consume_local_delivery(conversation_id, content):
            mark_local_delivery(payload.get("id"))  # also makes webhook retries idempotent
            save_message(row["school_id"], row["guardian_id"], "human", content)
            await broadcast(key(row["school_id"], row["guardian_id"]), {"sender": "human", "content": content})
            number = whatsapp.number_for(row["school_id"], row["guardian_id"])
            if number and content:
                await whatsapp.send_text(number, content)
    return {"ok": True}


@app.websocket("/ws/{school_id}/{guardian_id}")
async def websocket_endpoint(websocket: WebSocket, school_id: str, guardian_id: str):
    await websocket.accept()
    channel = key(school_id, guardian_id)
    clients[channel].add(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        clients[channel].discard(websocket)
