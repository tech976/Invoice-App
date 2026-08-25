"""Storing what the broker said.

There is nothing to look up. The parser reads the sentence into columns and
this writes them down as spoken — the codes, the goods and the numbers exactly
as they were dictated. No client list, no matching, and no connection to the
invoice ledger.
"""
from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import Trade
from app.voice.parse import ParsedTrade, parse_trade

log = logging.getLogger(__name__)


def parse_spoken_trade(transcript: str) -> ParsedTrade:
    """Read a spoken sentence into the columns of a trade."""
    return parse_trade(transcript)


def save_trade(db: Session, payload: dict, *, heard: str | None = None,
               parsed: dict | None = None) -> Trade:
    """Record a trade the broker has read back and confirmed.

    Only ever called after a person has seen the fields. Nothing spoken is
    written straight to the book.
    """
    quantity = payload.get("quantity")
    rate = payload.get("rate")
    value = None
    if quantity is not None and rate is not None:
        value = (Decimal(str(quantity)) * Decimal(str(rate))).quantize(Decimal("0.01"))

    def said(name: str) -> str | None:
        text = (payload.get(name) or "").strip()
        return text or None

    trade = Trade(
        traded_on=payload.get("traded_on") or date.today(),
        seller=said("seller"),
        buyer=said("buyer"),
        goods=said("goods"),
        quantity=quantity,
        uom=said("uom"),
        rate=rate,
        value=value,
        heard=heard,
        parsed=parsed,
        source=payload.get("source") or "voice",
        notes=said("notes"),
    )
    db.add(trade)
    db.flush()
    log.info("trade %s booked: %s -> %s, %s %s at %s",
             trade.id, trade.seller, trade.buyer, trade.quantity, trade.uom, trade.rate)
    return trade
