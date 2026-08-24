"""Parse the 'Amount in words' line into a number.

Nearly every Indian bill prints its total twice: once in digits and once in
words. The words are an independent encoding of the same figure, which makes
them the single best check available — if the digits were misread, the words
almost never agree.

    'INR Sixty One Lakh Thirty Nine Thousand Two Hundred Thirty Eight Only'
        -> Decimal('6139238')
"""
from __future__ import annotations

import re
from decimal import Decimal

_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "twenty": 20, "thirty": 30, "forty": 40, "fourty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}

# Indian scale words. 'arab' and 'kharab' show up on older bills.
_SCALES = {
    "hundred": 100,
    "thousand": 1_000,
    "lakh": 100_000, "lakhs": 100_000, "lac": 100_000, "lacs": 100_000,
    "crore": 10_000_000, "crores": 10_000_000,
    "arab": 1_000_000_000, "arabs": 1_000_000_000,
}

_NOISE = {
    "inr", "rs", "rupees", "rupee", "only", "and", "of", "the", "paisa",
    "paise", "ps", "rupess", "amount", "words", "in",
}

_TOKEN = re.compile(r"[a-z]+")


# 'Rupees and Seventy only' — the paise are named without the word 'paise'.
_RUPEES_AND = re.compile(r"\brupees?\b\s+and\s+(.+?)(?:\bonly\b|$)")


def _split_paise(lowered: str) -> tuple[str, str]:
    """Separate the rupee words from the paise words.

    Two spellings occur in the wild:
        '... Forty Four and Sixty Six paise Only'   -> explicit 'paise'
        '... Eighty-One Rupees and Seventy only'    -> implied by 'Rupees and'
    """
    if "paise" in lowered or "paisa" in lowered:
        head, _, _tail = lowered.rpartition("paise" if "paise" in lowered else "paisa")
        if " and " in head:
            rupee_text, _, paise_text = head.rpartition(" and ")
            return rupee_text, paise_text
        return head, ""

    match = _RUPEES_AND.search(lowered)
    if match:
        head = lowered[: match.start()]
        # Guard against a leading label like 'INR Rupees Nine Lakh ...' —
        # only treat this as a paise tail if real digits precede it.
        if any(t in _UNITS or t in _SCALES for t in _TOKEN.findall(head)):
            return head, match.group(1)

    return lowered, ""


def _parse_words(tokens: list[str]) -> Decimal | None:
    """Fold a token list using the Indian scale system."""
    total = Decimal(0)
    current = Decimal(0)
    seen_any = False

    for tok in tokens:
        if tok in _UNITS:
            current += _UNITS[tok]
            seen_any = True
        elif tok == "hundred":
            # 'nineteen hundred' is valid; a bare 'hundred' means one hundred.
            current = (current or Decimal(1)) * 100
            seen_any = True
        elif tok in _SCALES:
            scale = Decimal(_SCALES[tok])
            total += (current or Decimal(1)) * scale
            current = Decimal(0)
            seen_any = True
        # Anything else is noise and is skipped.

    if not seen_any:
        return None
    return total + current


def words_to_number(text: str | None) -> Decimal | None:
    """Parse a full amount-in-words line, including a paise tail."""
    if not text:
        return None

    lowered = str(text).lower()
    # Drop a leading label such as 'Amount Chargeable (in words):'.
    lowered = re.sub(r"^.*?\bwords?\b\s*[:\-]?", "", lowered) if "word" in lowered else lowered

    paise = Decimal(0)
    rupee_text, paise_text = _split_paise(lowered)
    if paise_text:
        paise_value = _parse_words([t for t in _TOKEN.findall(paise_text) if t not in _NOISE])
        if paise_value is not None:
            paise = paise_value
    lowered = rupee_text

    tokens = [t for t in _TOKEN.findall(lowered) if t not in _NOISE]
    rupees = _parse_words(tokens)
    if rupees is None:
        return None

    return rupees + (paise / Decimal(100))
