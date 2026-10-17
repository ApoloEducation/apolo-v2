import asyncio
from collections import defaultdict

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .agent import Escalate, answer
from .chatwoot import Chatwoot, ChatwootUnavailable
from .state import (conversation, history, initialize, mark_local_delivery, save_conversation,
                    save_message, was_locally_delivered)

app = FastAPI(title="Apolo Agent API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
clients: dict[str, set[WebSocket]] = defaultdict(set)
chatwoot = Chatwoot()
HANDOFF = "Vou encaminhar sua mensagem para nossa equipe de atendimento. Um atendente responderá por aqui assim que possível."


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
    import httpx, os
    async with httpx.AsyncClient() as client:
        return (await client.get(f"{os.getenv('SCHOOL_DATA_URL')}/guardians")).json()


@app.get("/v1/history/{guardian_id}")
def get_history(guardian_id: str):
    return history(guardian_id)


async def broadcast(guardian_id: str, item: dict):
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
    current = conversation(message.guardian_id)
    save_message(message.guardian_id, "guardian", message.content)
    await broadcast(message.guardian_id, {"sender": "guardian", "content": message.content})
    try:
        contact_id, conversation_id = await chatwoot.ensure_contact_and_conversation(message.guardian_id, message.guardian_name, current)
        mode = current["mode"] if current else "auto"
        save_conversation(message.guardian_id, conversation_id, contact_id, mode)
        await chatwoot.message(conversation_id, message.content, "incoming")
        if mode == "human":
            return {"status": "queued"}
        try:
            reply = await answer(message.guardian_id, message.content)
        except Escalate:
            reply = HANDOFF
            await chatwoot.escalate(conversation_id)
            save_conversation(message.guardian_id, conversation_id, contact_id, "human")
        outgoing = await chatwoot.message(conversation_id, reply, "outgoing")
        mark_local_delivery(outgoing.get("id"))
        save_message(message.guardian_id, "agent", reply)
        await broadcast(message.guardian_id, {"sender": "agent", "content": reply})
        return {"status": "answered", "reply": reply}
    except ChatwootUnavailable as exc:
        raise HTTPException(503, str(exc))


@app.post("/webhooks/chatwoot")
async def chatwoot_webhook(payload: dict):
    event = payload.get("event")
    conversation_payload = payload.get("conversation", {})
    conversation_id = conversation_payload.get("id") or payload.get("conversation_id")
    status = payload.get("status") or conversation_payload.get("status")
    if event == "conversation_status_changed" and status == "resolved":
        from .state import db
        with db() as conn:
            row = conn.execute("SELECT guardian_id FROM conversations WHERE chatwoot_conversation_id=?", (conversation_id,)).fetchone()
            if row:
                save_conversation(row["guardian_id"], conversation_id, None, "auto")
    if event == "message_created" and payload.get("message_type") == "outgoing" and not payload.get("private"):
        from .state import db
        with db() as conn:
            row = conn.execute("SELECT guardian_id FROM conversations WHERE chatwoot_conversation_id=?", (conversation_id,)).fetchone()
        if row and not was_locally_delivered(payload.get("id")):
            content = payload.get("content") or ""
            save_message(row["guardian_id"], "human", content)
            await broadcast(row["guardian_id"], {"sender": "human", "content": content})
    return {"ok": True}


@app.websocket("/ws/{guardian_id}")
async def websocket_endpoint(websocket: WebSocket, guardian_id: str):
    await websocket.accept()
    clients[guardian_id].add(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        clients[guardian_id].discard(websocket)
