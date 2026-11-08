import os
from collections import defaultdict

import httpx
from fastapi import FastAPI, HTTPException, Path, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .agent import Escalate, answer
from .chatwoot import Chatwoot, ChatwootUnavailable
from .state import (consume_local_delivery, conversation, conversation_by_chatwoot_id, expect_local_delivery, history, initialize, mark_local_delivery,
                    save_conversation, save_message, was_locally_delivered)

app = FastAPI(title="Apolo Agent API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",") if o.strip()],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
clients: dict[str, set[WebSocket]] = defaultdict(set)
chatwoot = Chatwoot()
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


@app.post("/v1/messages")
async def create_message(message: MessageIn):
    school, guardian = message.school_id, message.guardian_id
    if guardian not in {g["id"] for g in await school_data(f"/schools/{school}/guardians")}:
        raise HTTPException(404, "Responsável não encontrado")
    current = conversation(school, guardian)
    previous = history(school, guardian)  # before saving this message: it is passed to the model separately
    save_message(school, guardian, "guardian", message.content)
    await broadcast(key(school, guardian), {"sender": "guardian", "content": message.content})
    try:
        contact_id, conversation_id = await chatwoot.ensure_contact_and_conversation(school, guardian, message.guardian_name, current)
        mode = current["mode"] if current else "auto"
        save_conversation(school, guardian, conversation_id, contact_id, mode)
        await chatwoot.message(conversation_id, message.content, "incoming")
        if mode == "human":
            return {"status": "queued"}
        try:
            reply = await answer(school, guardian, message.content, previous)
        except Escalate:
            reply = HANDOFF
            await chatwoot.escalate(conversation_id)
            save_conversation(school, guardian, conversation_id, contact_id, "human")
        expect_local_delivery(conversation_id, reply)
        outgoing = await chatwoot.message(conversation_id, reply, "outgoing")
        mark_local_delivery(outgoing.get("id"))
        save_message(school, guardian, "agent", reply)
        await broadcast(key(school, guardian), {"sender": "agent", "content": reply})
        return {"status": "answered", "reply": reply}
    except ChatwootUnavailable as exc:
        raise HTTPException(503, str(exc))


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
