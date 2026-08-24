"""Map a line description to a canonical product.

Without this, 'Walnuts Inshell / 30-34', 'WALNUT INSHELL 30/34' and 'Akhrot
Inshell 30-34' are three different things and the commodity reports are
useless. Grade matters commercially — 30-34 and 36+ walnuts are different
prices — so the grade is part of the canonical identity, not noise.
"""
from __future__ import annotations

import logging

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.extraction.normalize import (
    clean_text,
    normalize_hsn,
    normalize_product_name,
    normalize_uom,
)
from app.models import Product, ProductAlias

log = logging.getLogger(__name__)

FUZZY_THRESHOLD = 90


def _identity(description: str | None, remarks: str | None) -> tuple[str, str]:
    """(full label, comparison key) for a line.

    The grade is appended only when it is not already part of the
    description — plenty of bills print 'Almonds - Solitaire Choco' in the
    name column and repeat 'Solitaire Choco' in the remarks column, and
    concatenating blindly gives a canonical name that says it twice.
    """
    name = clean_text(description) or ""
    grade = clean_text(remarks) or ""

    label = name
    if grade and normalize_product_name(grade) not in normalize_product_name(name):
        label = f"{name} {grade}".strip()

    return label, normalize_product_name(label)


def resolve_product(
    db: Session,
    *,
    description: str | None,
    item_remarks: str | None = None,
    hsn: str | None = None,
    uom: str | None = None,
    tax_rate=None,
    seller_id: int | None = None,
) -> Product | None:
    label, key = _identity(description, item_remarks)
    if not key:
        return None

    hsn = normalize_hsn(hsn)

    alias = db.scalar(select(ProductAlias).where(ProductAlias.normalized_alias == key))
    if alias:
        alias.seen_count = (alias.seen_count or 0) + 1
        return db.get(Product, alias.product_id)

    exact = db.scalar(select(Product).where(Product.normalized_name == key))
    if exact:
        return exact

    # Fuzzy, but only inside the same HSN *and* the same grade.
    #
    # HSN alone keeps almonds from matching walnuts. The grade matters just as
    # much: 'Walnuts Inshell 30-34' and 'Walnuts Inshell 36+' differ by two
    # characters out of twenty and would fuzzy-match on any sane threshold,
    # yet they are different goods at different prices. Collapsing them would
    # quietly corrupt every per-product rate and volume report, so a differing
    # grade blocks the match outright.
    grade_key = normalize_product_name(item_remarks)
    if hsn:
        candidates = [
            c
            for c in db.scalars(select(Product).where(Product.default_hsn == hsn)).all()
            if normalize_product_name(c.grade) == grade_key
        ]
        best, best_score = None, 0.0
        for cand in candidates:
            score = fuzz.token_set_ratio(key, cand.normalized_name)
            if score > best_score:
                best, best_score = cand, score
        if best is not None and best_score >= FUZZY_THRESHOLD:
            db.add(
                ProductAlias(
                    product_id=best.id,
                    alias=label,
                    normalized_alias=key,
                    seller_id=seller_id,
                )
            )
            log.info("fuzzy-matched product %r to %s (%.0f)", label, best.id, best_score)
            return best

    product = Product(
        canonical_name=label,
        normalized_name=key,
        grade=clean_text(item_remarks),
        default_hsn=hsn,
        default_uom=normalize_uom(uom),
        default_tax_rate=tax_rate,
        category=_guess_category(key, hsn),
    )
    db.add(product)
    db.flush()
    log.info("created product %s %r", product.id, label)
    return product


# Coarse buckets so the dashboard can group turnover without a human tagging
# every new SKU. HSN chapter 08 is edible nuts and fruit.
_CATEGORY_HINTS = (
    ("almond", "Almonds"), ("badam", "Almonds"),
    ("walnut", "Walnuts"), ("akhrot", "Walnuts"),
    ("cashew", "Cashew"), ("kaju", "Cashew"),
    ("pistachio", "Pistachio"), ("pista", "Pistachio"),
    ("raisin", "Raisins"), ("kishmish", "Raisins"),
    ("date", "Dates"), ("khajur", "Dates"),
    ("fig", "Figs"), ("anjeer", "Figs"),
    ("apricot", "Apricot"), ("hazelnut", "Hazelnut"),
    ("packing", "Charges"), ("labour", "Charges"), ("freight", "Charges"),
)


def _guess_category(key: str, hsn: str | None) -> str | None:
    for needle, category in _CATEGORY_HINTS:
        if needle in key:
            return category
    if hsn and hsn.startswith("0802"):
        return "Nuts"
    if hsn and hsn.startswith("08"):
        return "Dry Fruits"
    return None
