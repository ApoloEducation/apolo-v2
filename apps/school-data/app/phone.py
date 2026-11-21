"""Phone numbers as WhatsApp reports them: digits only, with the country code (wa_id), e.g. 5537999242395."""
import re


def normalize(raw: str, assume_brazil: bool = True) -> str:
    """Staff may type '(37) 99924-2395' or '+55 37 99924-2395'. Without a '+', 10-11 digits are taken as Brazilian
    (country code 55 added). Raises ValueError with a Portuguese message when it cannot be a phone number."""
    text = raw.strip()
    digits = re.sub(r"\D", "", text)
    if assume_brazil and not text.startswith("+") and len(digits) in (10, 11):
        digits = "55" + digits
    if not 10 <= len(digits) <= 15:
        raise ValueError("telefone inválido: informe DDD e número (ex.: (37) 99924-2395) ou +código do país")
    return digits


def variants(number: str) -> set[str]:
    """Brazilian mobiles exist with and without the extra 9 after the area code, and Meta may report either."""
    found = {number}
    if number.startswith("55"):
        if len(number) == 13 and number[4] == "9":
            found.add(number[:4] + number[5:])
        elif len(number) == 12:
            found.add(number[:4] + "9" + number[4:])
    return found
