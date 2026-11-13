"""WhatsApp Cloud API (Meta) helpers: sender allow-list, signature check and outbound text."""
import hashlib
import hmac
import logging
import os

import httpx

log = logging.getLogger("whatsapp")
GRAPH_URL = "https://graph.facebook.com/v21.0"
MAX_LEN = 4096


def br_variant(number: str) -> str | None:
    """Brazilian mobiles exist with and without the extra 9 after the area code; Meta reports the
    sender without it (5537 9xxx xxxx) but may only accept sends to the form with it. None if not Brazilian."""
    if not number.startswith("55") or not number.isdigit():
        return None
    if len(number) == 13 and number[4] == "9":
        return number[:4] + number[5:]
    if len(number) == 12:
        return number[:4] + "9" + number[4:]
    return None


def allowed_numbers() -> dict[str, tuple[str, str]]:
    """WHATSAPP_ALLOWED_NUMBERS='5511999999999:horizonte:g01,...' -> {wa_id: (school_id, guardian_id)}.
    Each Brazilian number is also accepted in its other form (see br_variant)."""
    result = {}
    for item in os.getenv("WHATSAPP_ALLOWED_NUMBERS", "").split(","):
        parts = [p.strip() for p in item.split(":")]
        if len(parts) == 3 and all(parts):
            result[parts[0].lstrip("+")] = (parts[1], parts[2])
    for number, target in list(result.items()):
        variant = br_variant(number)
        if variant:
            result.setdefault(variant, target)
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
                # 131030 = recipient not in the test allow-list; for Brazil, retry with the other number form.
                if response.status_code == 400 and "131030" in response.text and br_variant(to):
                    response = await client.post(url, json={**payload, "to": br_variant(to)}, headers=headers)
                if response.status_code >= 400:
                    log.error("WhatsApp send failed %s: %s", response.status_code, response.text)
            except httpx.HTTPError as exc:
                log.error("WhatsApp send error: %s", exc)
