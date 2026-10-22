"""WhatsApp Cloud API (Meta) channel.

Inbound: Meta posts to /webhooks/whatsapp -> we map the sender's phone to a guardian id and run the exact same
pipeline the Mini-WhatsApp uses. Outbound: every agent/human message broadcast for that guardian is also sent
through the Graph API (see `deliver`, called from main.broadcast).
"""
import hashlib
import hmac
import logging
import os
import re

import httpx

log = logging.getLogger("apolo.whatsapp")

MAX_TEXT = 4000  # WhatsApp text bodies are limited to 4096 characters
UNKNOWN_NUMBER = ("Olá! Não encontrei este número no cadastro da escola. "
                  "Por favor, entre em contato com a secretaria para atualizar seu telefone.")
UNSUPPORTED = "Por enquanto consigo responder apenas mensagens de texto. Pode escrever sua dúvida?"


def digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def phone_keys(number: str) -> set[str]:
    """Equivalent spellings of one number. Meta often reports Brazilian mobiles without the ninth digit."""
    n = digits(number)
    keys = {n}
    if n.startswith("55") and len(n) == 13 and n[4] == "9":
        keys.add(n[:4] + n[5:])
    elif n.startswith("55") and len(n) == 12:
        keys.add(n[:4] + "9" + n[4:])
    return keys


def phone_map() -> dict[str, str]:
    """WHATSAPP_GUARDIAN_PHONES='5511999990001=g01,5511988880002=g02' -> {phone variant: guardian id}."""
    mapping: dict[str, str] = {}
    for pair in os.getenv("WHATSAPP_GUARDIAN_PHONES", "").split(","):
        if "=" not in pair:
            continue
        phone, guardian_id = pair.split("=", 1)
        for key in phone_keys(phone):
            mapping[key] = guardian_id.strip()
    return mapping


def guardian_for_phone(phone: str) -> str | None:
    mapping = phone_map()
    return next((mapping[k] for k in phone_keys(phone) if k in mapping), None)


def phone_for_guardian(guardian_id: str) -> str | None:
    for pair in os.getenv("WHATSAPP_GUARDIAN_PHONES", "").split(","):
        if "=" in pair:
            phone, gid = pair.split("=", 1)
            if gid.strip() == guardian_id:
                return digits(phone)
    return None


def enabled() -> bool:
    return bool(os.getenv("WHATSAPP_ACCESS_TOKEN") and os.getenv("WHATSAPP_PHONE_NUMBER_ID"))


def valid_signature(body: bytes, header: str | None) -> bool:
    """Checks X-Hub-Signature-256. Skipped (with the app secret unset) so local experiments still work."""
    secret = os.getenv("WHATSAPP_APP_SECRET")
    if not secret:
        return True
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header or "")


def parse_incoming(payload: dict) -> list[dict]:
    """Flattens Meta's nested webhook payload into [{id, phone, type, text, name}]; status callbacks are ignored."""
    found = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            names = {c.get("wa_id"): (c.get("profile") or {}).get("name") for c in value.get("contacts", [])}
            for msg in value.get("messages", []):
                found.append({"id": msg.get("id"), "phone": msg.get("from", ""), "type": msg.get("type"),
                              "text": (msg.get("text") or {}).get("body", ""), "name": names.get(msg.get("from"))})
    return found


async def send_text(phone: str, text: str) -> None:
    version = os.getenv("WHATSAPP_API_VERSION", "v21.0")
    url = f"https://graph.facebook.com/{version}/{os.getenv('WHATSAPP_PHONE_NUMBER_ID')}/messages"
    headers = {"Authorization": f"Bearer {os.getenv('WHATSAPP_ACCESS_TOKEN')}"}
    async with httpx.AsyncClient(timeout=15) as client:
        for start in range(0, max(len(text), 1), MAX_TEXT):
            body = {"messaging_product": "whatsapp", "to": digits(phone), "type": "text",
                    "text": {"body": text[start:start + MAX_TEXT], "preview_url": False}}
            response = await client.post(url, headers=headers, json=body)
            if response.status_code >= 400:
                # Typical causes: expired token, recipient not on the test list, or the 24h service window closed.
                log.error("envio WhatsApp falhou (%s): %s", response.status_code, response.text)
                return


async def deliver(guardian_id: str, item: dict) -> None:
    """Forward agent/human messages to the parent's WhatsApp. Never raises: the web chat must not depend on it."""
    if item.get("sender") == "guardian" or not enabled():
        return
    phone = phone_for_guardian(guardian_id)
    if not phone:
        return
    try:
        await send_text(phone, item["content"])
    except Exception:  # noqa: BLE001
        log.exception("falha ao enviar mensagem WhatsApp gid=%s", guardian_id)
