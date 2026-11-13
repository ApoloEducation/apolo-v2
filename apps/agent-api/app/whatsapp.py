"""WhatsApp Cloud API (Meta) helpers: sender allow-list, signature check and outbound text."""
import hashlib
import hmac
import logging
import os

import httpx

log = logging.getLogger("whatsapp")
GRAPH_URL = "https://graph.facebook.com/v21.0"
MAX_LEN = 4096


def allowed_numbers() -> dict[str, tuple[str, str]]:
    """WHATSAPP_ALLOWED_NUMBERS='5511999999999:horizonte:g01,...' -> {wa_id: (school_id, guardian_id)}."""
    result = {}
    for item in os.getenv("WHATSAPP_ALLOWED_NUMBERS", "").split(","):
        parts = [p.strip() for p in item.split(":")]
        if len(parts) == 3 and all(parts):
            result[parts[0].lstrip("+")] = (parts[1], parts[2])
    return result


def number_for(school_id: str, guardian_id: str) -> str | None:
    for number, target in allowed_numbers().items():
        if target == (school_id, guardian_id):
            return number
    return None


def valid_signature(body: bytes, header: str | None) -> bool:
    secret = os.getenv("WHATSAPP_APP_SECRET")
    if not secret:
        return True  # signature check is opt-in (test mode)
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


def configured() -> bool:
    return bool(os.getenv("META_API_TOKEN") and os.getenv("WHATSAPP_PHONE_NUMBER_ID"))


async def send_text(to: str, body: str) -> None:
    if not configured():
        log.warning("WhatsApp not configured (META_API_TOKEN / WHATSAPP_PHONE_NUMBER_ID); message to %s dropped", to)
        return
    url = f"{GRAPH_URL}/{os.getenv('WHATSAPP_PHONE_NUMBER_ID')}/messages"
    headers = {"Authorization": f"Bearer {os.getenv('META_API_TOKEN')}"}
    async with httpx.AsyncClient(timeout=15) as client:
        for start in range(0, len(body), MAX_LEN):
            payload = {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": body[start:start + MAX_LEN]}}
            try:
                response = await client.post(url, json=payload, headers=headers)
                if response.status_code >= 400:
                    log.error("WhatsApp send failed %s: %s", response.status_code, response.text)
            except httpx.HTTPError as exc:
                log.error("WhatsApp send error: %s", exc)
