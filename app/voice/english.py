"""Saying back, in English, the trade that was understood.

The broker speaks Hindi, Marathi or English and the book is kept in English,
so something has to bridge the two on screen. This writes that bridge.

It is built from the fields that were read, not by translating the sentence
with a model. That is deliberate. A model asked to translate 'नऊ हजार चारशे
दराने' offered "three million two hundred thousand one hundred and eighty-five
dollars" — fluent, confident and wrong. Composed from the parsed values
instead, the sentence cannot say anything the fields do not, so if it reads
back correctly then what is about to be saved is correct, and if a field was
misheard the sentence shows exactly that rather than papering over it.

So this is not a second opinion. It is the same reading, in words the broker
can check at a glance.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

# What one of each unit is called, so the sentence reads 'per bag' rather
# than 'per BAGS'.
SINGULAR = {
    "BAGS": "bag", "KGS": "kg", "QTL": "quintal", "MT": "tonne",
    "BOX": "box", "PCS": "piece",
}
PLURAL = {
    "BAGS": "bags", "KGS": "kg", "QTL": "quintal", "MT": "tonnes",
    "BOX": "boxes", "PCS": "pieces",
}


def _value(parsed: dict, name: str):
    guess = parsed.get(name)
    return guess.get("value") if isinstance(guess, dict) else None


def indian_group(number: Decimal) -> str:
    """1250 -> '1,250' and 4125000 -> '41,25,000' — the grouping used here.

    The same 2-2-3 grouping the invoice side of the app uses, so a figure
    read off this screen can be compared against a bill character by
    character.
    """
    negative = number < 0
    whole, _, frac = f"{abs(number):.2f}".partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts + [tail])
    if frac == "00":
        return ("-" if negative else "") + whole
    return ("-" if negative else "") + f"{whole}.{frac}"


def _number(value) -> str | None:
    try:
        return indian_group(Decimal(str(value)))
    except (InvalidOperation, TypeError, ValueError):
        return None


def sentence(parsed: dict) -> str | None:
    """The trade as one English sentence, or None if too little was read.

    Every part is omitted rather than invented when it was not heard, so a
    half-read sentence looks half-read.
    """
    seller = _value(parsed, "seller")
    buyer = _value(parsed, "buyer")
    goods = _value(parsed, "goods")
    quantity = _value(parsed, "quantity")
    uom = _value(parsed, "uom")
    rate = _value(parsed, "rate")

    quantity_text = _number(quantity)
    rate_text = _number(rate)

    # A sentence naming neither side nor the goods is not worth showing.
    if not any((seller, buyer, goods, quantity_text, rate_text)):
        return None

    parts: list[str] = []
    if seller and buyer:
        parts.append(f"{seller} sold {buyer}")
    elif seller:
        parts.append(f"{seller} sold")
    elif buyer:
        parts.append(f"Sold to {buyer}")
    else:
        parts.append("Sold")

    amount: list[str] = []
    if quantity_text:
        unit = PLURAL.get(uom or "", (uom or "").lower())
        amount.append(f"{quantity_text} {unit}".strip())
    if goods:
        amount.append(f"of {goods}" if quantity_text else str(goods))
    if amount:
        parts.append(" ".join(amount))

    if rate_text:
        unit = SINGULAR.get(uom or "")
        parts.append(f"at {rate_text} per {unit}" if unit else f"at {rate_text}")

    text = " ".join(parts).strip()
    total = _total(quantity, rate)
    if total:
        text += f" — {total}"
    return text + "."


def _total(quantity, rate) -> str | None:
    """Quantity times rate, which is the figure the money actually turns on."""
    if quantity is None or rate is None:
        return None
    try:
        return indian_group(Decimal(str(quantity)) * Decimal(str(rate)))
    except (InvalidOperation, TypeError, ValueError):
        return None
