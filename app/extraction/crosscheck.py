"""Compare two independent readings of the same bill.

The arithmetic rules in `validation/rules.py` are strong on money and blind on
identity: nothing checks whether the seller and buyer were read the right way
round, whether the broker's name is right, or whether the date is the printed
one. Those fields carry no internal redundancy, so no amount of rule-writing
will verify them.

What does verify them is a second, independent reading. Two models that agree
on a field are unlikely to have invented the same wrong answer; where they
disagree, a human decides. That converts "we hope the model read it right"
into "two readers agreed, or you were told".
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.extraction.normalize import (
    clean_gstin,
    clean_text,
    parse_amount,
    parse_date,
)
from app.schemas import ExtractedInvoice


def _money(v):
    a = parse_amount(v)
    return None if a is None else a.quantize(Decimal("0.01"))


def _text(v):
    t = clean_text(v)
    return t.lower() if t else None


def _date(v):
    return parse_date(v)


def _party_name(p):
    return _text(getattr(p, "name", None))


def _party_gstin(p):
    return clean_gstin(getattr(p, "gstin", None))


# The fields worth a second opinion: everything money-critical, plus the
# identity fields the rules cannot reach.
FIELDS = [
    ("invoice_number", "invoice number", lambda i: _text(i.invoice_number)),
    ("invoice_date", "invoice date", lambda i: _date(i.invoice_date)),
    ("seller.name", "seller", lambda i: _party_name(i.seller)),
    ("seller.gstin", "seller GSTIN", lambda i: _party_gstin(i.seller)),
    ("buyer.name", "buyer", lambda i: _party_name(i.buyer)),
    ("buyer.gstin", "buyer GSTIN", lambda i: _party_gstin(i.buyer)),
    ("broker_name", "broker", lambda i: _text(i.broker_name)),
    ("taxable_value", "taxable value", lambda i: _money(i.taxable_value)),
    ("grand_total", "grand total", lambda i: _money(i.grand_total)),
    ("total_quantity", "total quantity", lambda i: _money(i.total_quantity)),
]


@dataclass
class Disagreement:
    field_path: str
    label: str
    first: str | None
    second: str | None


def _fmt(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return f"{value:,.2f}"
    return str(value)


def compare_readings(
    first: ExtractedInvoice, second: ExtractedInvoice
) -> list[Disagreement]:
    """Every field where two readings of the same bill differ."""
    out: list[Disagreement] = []

    for path, label, get in FIELDS:
        a, b = get(first), get(second)
        if a != b:
            out.append(Disagreement(path, label, _fmt(a), _fmt(b)))

    if len(first.lines) != len(second.lines):
        out.append(
            Disagreement(
                "lines",
                "number of line items",
                str(len(first.lines)),
                str(len(second.lines)),
            )
        )
    else:
        for idx, (l1, l2) in enumerate(zip(first.lines, second.lines), start=1):
            for attr, label in (
                ("taxable_amount", "amount"),
                ("quantity", "quantity"),
                ("rate", "rate"),
            ):
                a, b = _money(getattr(l1, attr)), _money(getattr(l2, attr))
                if a != b:
                    out.append(
                        Disagreement(
                            f"lines.{idx}.{attr}", f"line {idx} {label}", _fmt(a), _fmt(b)
                        )
                    )
            a, b = _text(l1.description), _text(l2.description)
            if a != b:
                out.append(
                    Disagreement(
                        f"lines.{idx}.description", f"line {idx} description", _fmt(a), _fmt(b)
                    )
                )

    return out
