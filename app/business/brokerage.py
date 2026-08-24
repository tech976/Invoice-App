"""Brokerage accrual.

The broker earns a percentage of each bill they arrange and settles it with
the parties at year end. Accruing it per invoice at extraction time is what
turns 'how much am I owed for 26-27' from an evening with a calculator into a
query.

Rule resolution is most-specific-wins: a rate agreed for one seller-buyer pair
on one commodity beats a rate for that pair on anything, which beats the
seller's standing rate, which beats the house default.
"""
from __future__ import annotations

import logging
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import BrokerageEntry, BrokerageRule, Invoice

log = logging.getLogger(__name__)

BASIS_FIELDS = {
    "taxable_value": "taxable_value",
    "grand_total": "grand_total",
    "subtotal": "subtotal",
}


def _specificity(rule: BrokerageRule) -> int:
    """Higher wins. Product beats HSN, and any pair beats a single party."""
    score = 0
    if rule.seller_id:
        score += 4
    if rule.buyer_id:
        score += 4
    if rule.product_id:
        score += 2
    if rule.hsn:
        score += 1
    return score


def _applies(rule: BrokerageRule, invoice: Invoice, hsns: set[str], product_ids: set[int]) -> bool:
    if not rule.is_active:
        return False
    if rule.seller_id and rule.seller_id != invoice.seller_id:
        return False
    if rule.buyer_id and rule.buyer_id != invoice.buyer_id:
        return False
    if rule.product_id and rule.product_id not in product_ids:
        return False
    if rule.hsn and rule.hsn not in hsns:
        return False
    if invoice.invoice_date:
        if rule.effective_from and invoice.invoice_date < rule.effective_from:
            return False
        if rule.effective_to and invoice.invoice_date > rule.effective_to:
            return False
    return True


def find_rule(db: Session, invoice: Invoice) -> BrokerageRule | None:
    hsns = {l.hsn for l in invoice.lines if l.hsn}
    product_ids = {l.product_id for l in invoice.lines if l.product_id}
    candidates = [
        r
        for r in db.scalars(select(BrokerageRule).where(BrokerageRule.is_active.is_(True))).all()
        if _applies(r, invoice, hsns, product_ids)
    ]
    if not candidates:
        return None
    return max(candidates, key=_specificity)


def compute_brokerage(db: Session, invoice: Invoice) -> BrokerageEntry | None:
    """Accrue brokerage for one invoice, replacing any previous accrual.

    A settled entry is left alone — money already collected is not rewritten
    because a bill was re-read.
    """
    entry = db.scalar(select(BrokerageEntry).where(BrokerageEntry.invoice_id == invoice.id))
    if entry and entry.status == "settled":
        return entry

    rule = find_rule(db, invoice)
    basis_field = BASIS_FIELDS.get(rule.basis if rule else "taxable_value", "taxable_value")
    basis = getattr(invoice, basis_field, None) or invoice.taxable_value or invoice.grand_total
    if basis is None:
        return None
    basis = Decimal(str(basis))

    if rule and rule.rate_per_unit and invoice.total_quantity:
        amount = Decimal(str(rule.rate_per_unit)) * Decimal(str(invoice.total_quantity))
        rate_pct = None
    else:
        rate_pct = Decimal(str(rule.rate_pct)) if rule and rule.rate_pct is not None else Decimal(
            str(settings.default_brokerage_pct)
        )
        amount = basis * rate_pct / Decimal(100)

    if entry is None:
        entry = BrokerageEntry(invoice_id=invoice.id)
        db.add(entry)

    entry.rule_id = rule.id if rule else None
    entry.broker_id = invoice.broker_id
    entry.basis_amount = basis
    entry.rate_pct = rate_pct
    entry.amount = amount.quantize(Decimal("0.01"))
    entry.payable_by = rule.payable_by if rule else "seller"
    entry.financial_year = invoice.financial_year
    if entry.status != "invoiced":
        entry.status = "accrued"

    log.debug(
        "brokerage on invoice %s: %s of %s = %s",
        invoice.id, f"{rate_pct}%" if rate_pct is not None else "per-unit", basis, entry.amount,
    )
    return entry
