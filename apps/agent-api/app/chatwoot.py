import json
import logging
import os
from pathlib import Path

import httpx

log = logging.getLogger("apolo.chatwoot")


class ChatwootUnavailable(RuntimeError):
    pass


class Chatwoot:
    def __init__(self):
        self.base_url = os.getenv("CHATWOOT_URL", "http://chatwoot:3000")
        self.config_path = Path(os.getenv("CHATWOOT_CONFIG_PATH", "/bootstrap/integration.json"))

    def config(self) -> dict:
        if not self.config_path.exists():
            raise ChatwootUnavailable("Integração do Chatwoot ainda está sendo preparada.")
        return json.loads(self.config_path.read_text(encoding="utf-8"))

    async def request(self, method: str, path: str, payload: dict | None = None) -> dict:
        cfg = self.config()
        headers = {"api_access_token": cfg["api_access_token"]}
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.request(method, f"{self.base_url}/api/v1/accounts/{cfg['account_id']}{path}", headers=headers, json=payload)
            response.raise_for_status()
            return response.json() if response.content else {}

    async def ensure_contact_and_conversation(self, guardian_id: str, guardian_name: str, existing: dict | None) -> tuple[int, int]:
        cfg = self.config()
        if existing and existing.get("chatwoot_conversation_id") and existing.get("chatwoot_contact_id"):
            return existing["chatwoot_contact_id"], existing["chatwoot_conversation_id"]
        contact = await self.request("POST", "/contacts", {"name": guardian_name, "identifier": guardian_id, "custom_attributes": {"demo_guardian_id": guardian_id}})
        contact_id = contact["payload"]["contact"]["id"] if "payload" in contact else contact["id"]
        contact_inbox = await self.request("POST", f"/contacts/{contact_id}/contact_inboxes", {"inbox_id": cfg["inbox_id"], "source_id": guardian_id})
        source_id = contact_inbox.get("source_id", guardian_id)
        created = await self.request("POST", "/conversations", {"source_id": source_id, "inbox_id": cfg["inbox_id"], "contact_id": contact_id})
        return contact_id, created["id"]

    async def message(self, conversation_id: int, content: str, message_type: str = "incoming", bot: bool = False) -> dict:
        payload = {"content": content, "message_type": message_type, "private": False}
        if bot:
            payload["content_attributes"] = {"apolo_bot": True}
        return await self.request("POST", f"/conversations/{conversation_id}/messages", payload)

    async def escalate(self, conversation_id: int, category: str = "outros", reason: str = "", question: str = "") -> None:
        """Best effort: a Chatwoot hiccup here must never stop the parent from being told about the handoff."""
        cfg = self.config()
        steps = [
            ("labels", f"/conversations/{conversation_id}/labels", {"labels": ["escalado-humano", f"cat-{category}"]}),
            ("note", f"/conversations/{conversation_id}/messages", {"content": f"Categoria: {category}\nMotivo: {reason or 'n/d'}\nPergunta: {question}", "message_type": "outgoing", "private": True}),
        ]
        if cfg.get("team_id"):
            steps.append(("assignment", f"/conversations/{conversation_id}/assignments", {"team_id": cfg["team_id"], "assignee_id": cfg["human_user_id"]}))
        else:
            steps.append(("assignment", f"/conversations/{conversation_id}/assignments", {"assignee_id": cfg["human_user_id"]}))
        steps.append(("status", f"/conversations/{conversation_id}/toggle_status", {"status": "open"}))
        for name, path, payload in steps:
            try:
                await self.request("POST", path, payload)
            except Exception as exc:  # noqa: BLE001
                log.error("escalate: passo '%s' falhou: %s", name, exc)
