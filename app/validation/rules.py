"""Deterministic checks on an extracted invoice.

The model reads the bill; this module decides whether to believe it. Every
rule here is arithmetic or format checking with no model involved, so a
failure is a fact rather than an opinion.

An invoice carrying an unresolved `error` flag cannot be confirmed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.config import settings
from app.extraction.normalize import (
    STATE_CODES,
    normalize_hsn,
    state_from_gstin,
    validate_gstin,
)
from app.validation.words import words_to_number

TOL = Decimal(str(settings.amount_tolerance))
# Percentages and per-unit rates carry rounding noise from the vendor's own
# software, so line-level checks get a slightly wider band.
LINE_TOL = TOL * 2

ERROR = "error"
WARNING = "warning"
INFO = "info"


@dataclass
class Flag:
    rule: str
    severity: str
    message: str
    field_path: str | None = None
    expected: str | None = None
    actual: str | None = None


def _d(value) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001
        return None


def _sum(values) -> Decimal:
    total = Decimal(0)
    for v in values:
        d = _d(v)
        if d is not None:
            total += d
    return total


def _money(value: Decimal | None) -> str:
    return "-" if value is None else f"{value:,.2f}"


# --------------------------------------------------------------------------
# Individual rule groups
# --------------------------------------------------------------------------


def _check_required(inv) -> list[Flag]:
    flags: list[Flag] = []
    required = [
        ("invoice_number", inv.invoice_number, "Invoice number"),
        ("invoice_date", inv.invoice_date, "Invoice date"),
        ("grand_total", inv.grand_total, "Grand total"),
        ("seller_id", inv.seller_id, "Seller"),
        ("buyer_id", inv.buyer_id, "Buyer"),
    ]
    for path, value, label in required:
        if value in (None, ""):
            flags.append(
                Flag(
                    rule="missing_required_field",
                    severity=ERROR,
                    field_path=path,
                    message=f"{label} could not be read from the bill.",
                )
            )
    if not inv.lines:
        flags.append(
            Flag(
                rule="no_line_items",
                severity=ERROR,
                field_path="lines",
                message="No line items were extracted from this bill.",
            )
        )
    return flags


def _check_gstins(inv) -> list[Flag]:
    flags: list[Flag] = []
    for role, party in (
        ("seller", inv.seller),
        ("buyer", inv.buyer),
        ("consignee", inv.consignee),
    ):
        if party is None or not party.gstin:
            continue
        cleaned, problems = validate_gstin(party.gstin)
        for problem in problems:
            flags.append(
                Flag(
                    rule="gstin_invalid",
                    severity=ERROR if "checksum" in problem else WARNING,
                    field_path=f"{role}.gstin",
                    message=f"{role.title()} GSTIN {cleaned}: {problem}",
                    actual=cleaned,
                )
            )
        code, name = state_from_gstin(cleaned)
        if code and party.state_code and party.state_code != code:
            flags.append(
                Flag(
                    rule="state_code_mismatch",
                    severity=WARNING,
                    field_path=f"{role}.state_code",
                    message=(
                        f"{role.title()} state code {party.state_code} does not match "
                        f"the GSTIN prefix {code} ({name})."
                    ),
                    expected=code,
                    actual=party.state_code,
                )
            )
    return flags


def _check_line_arithmetic(inv) -> list[Flag]:
    """qty x rate, less any discount, should equal the row's amount."""
    flags: list[Flag] = []
    for line in inv.lines:
        qty, rate = _d(line.quantity), _d(line.rate)
        amount = _d(line.taxable_amount)
        if qty is None or rate is None or amount is None:
            continue

        gross = qty * rate
        expected = gross
        disc_pct = _d(line.discount_pct)
        disc_amt = _d(line.discount_amount)
        if disc_pct:
            expected = gross * (Decimal(1) - disc_pct / Decimal(100))
        elif disc_amt:
            expected = gross - abs(disc_amt)

        if abs(expected - amount) > LINE_TOL:
            flags.append(
                Flag(
                    rule="line_arithmetic",
                    severity=WARNING,
                    field_path=f"lines.{line.line_no}.taxable_amount",
                    message=(
                        f"Line {line.line_no} ({(line.description or '')[:40]}): "
                        f"{qty:,.3f} x {rate:,.4f}"
                        + (f" less {disc_pct}%" if disc_pct else "")
                        + f" = {expected:,.2f}, but the bill shows {amount:,.2f} "
                        f"(off by {abs(expected - amount):,.2f})."
                    ),
                    expected=_money(expected.quantize(Decimal("0.01"))),
                    actual=_money(amount),
                )
            )
    return flags


def _charge_totals(inv) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """(discounts, additions, round_off, tcs) from the charge rows."""
    discounts = Decimal(0)
    additions = Decimal(0)
    round_off = Decimal(0)
    tcs = Decimal(0)
    for charge in inv.charges:
        amount = _d(charge.amount) or Decimal(0)
        if charge.kind == "discount":
            discounts += -abs(amount)
        elif charge.kind == "round_off":
            round_off += amount
        elif charge.kind in ("tcs", "tds"):
            tcs += amount
        else:
            additions += amount
    return discounts, additions, round_off, tcs


def _check_totals(inv) -> list[Flag]:
    """Reconcile lines -> taxable value -> grand total.

    Bills differ on whether packing/freight is taxed with the goods or added
    after tax, so each check tries the plausible arrangements and only
    complains when none of them balance.
    """
    flags: list[Flag] = []
    lines_sum = _sum(l.taxable_amount for l in inv.lines)
    discounts, additions, charge_round, charge_tcs = _charge_totals(inv)

    taxable = _d(inv.taxable_value)
    if taxable is not None and inv.lines:
        candidates = {
            "lines": lines_sum,
            "lines + discount": lines_sum + discounts,
            "lines + discount + charges": lines_sum + discounts + additions,
            "lines + charges": lines_sum + additions,
        }
        best_label, best = min(candidates.items(), key=lambda kv: abs(kv[1] - taxable))
        if abs(best - taxable) > TOL:
            flags.append(
                Flag(
                    rule="taxable_value_mismatch",
                    severity=ERROR,
                    field_path="taxable_value",
                    message=(
                        f"Line amounts do not add up to the taxable value. "
                        f"Closest arrangement ({best_label}) gives {best:,.2f} "
                        f"against a printed {taxable:,.2f} — a gap of "
                        f"{abs(best - taxable):,.2f}."
                    ),
                    expected=_money(taxable),
                    actual=_money(best),
                )
            )

    grand = _d(inv.grand_total)
    if grand is not None:
        taxes = _sum([inv.cgst_amount, inv.sgst_amount, inv.igst_amount, inv.cess_amount])
        tcs = _d(inv.tcs_amount) or charge_tcs
        round_off = _d(inv.round_off)
        if round_off is None:
            round_off = charge_round
        other = _d(inv.other_charges)
        if other is None:
            other = additions

        base = taxable if taxable is not None else lines_sum + discounts
        candidates = {
            "taxable + tax + charges + round off": base + taxes + other + tcs + round_off,
            "taxable + tax + round off": base + taxes + tcs + round_off,
            "taxable + tax + charges": base + taxes + other + tcs,
        }
        best_label, best = min(candidates.items(), key=lambda kv: abs(kv[1] - grand))
        if abs(best - grand) > TOL:
            flags.append(
                Flag(
                    rule="grand_total_mismatch",
                    severity=ERROR,
                    field_path="grand_total",
                    message=(
                        f"The parts do not add up to the grand total. Closest "
                        f"arrangement ({best_label}) gives {best:,.2f} against a "
                        f"printed {grand:,.2f} — a gap of {abs(best - grand):,.2f}."
                    ),
                    expected=_money(grand),
                    actual=_money(best),
                )
            )

    if inv.total_quantity is not None and inv.lines:
        qty_sum = _sum(l.quantity for l in inv.lines)
        printed = _d(inv.total_quantity)
        if printed is not None and abs(qty_sum - printed) > Decimal("0.01"):
            flags.append(
                Flag(
                    rule="quantity_mismatch",
                    severity=WARNING,
                    field_path="total_quantity",
                    message=(
                        f"Line quantities total {qty_sum:,.3f} but the bill's "
                        f"total says {printed:,.3f}."
                    ),
                    expected=f"{printed:,.3f}",
                    actual=f"{qty_sum:,.3f}",
                )
            )
    return flags


def _check_tax_summary(inv) -> list[Flag]:
    """The HSN-wise table at the foot must agree with the invoice totals."""
    flags: list[Flag] = []
    if not inv.tax_rows:
        return flags

    for idx, row in enumerate(inv.tax_rows, start=1):
        base = _d(row.taxable_value)
        if base is None:
            continue
        for label, rate, amount in (
            ("CGST", _d(row.cgst_rate), _d(row.cgst_amount)),
            ("SGST", _d(row.sgst_rate), _d(row.sgst_amount)),
            ("IGST", _d(row.igst_rate), _d(row.igst_amount)),
        ):
            if rate is None or amount is None or rate == 0:
                continue
            expected = base * rate / Decimal(100)
            if abs(expected - amount) > TOL:
                flags.append(
                    Flag(
                        rule="tax_computation",
                        severity=WARNING,
                        field_path=f"tax_summary.{idx}.{label.lower()}_amount",
                        message=(
                            f"HSN {row.hsn or '?'}: {rate}% {label} on "
                            f"{base:,.2f} is {expected:,.2f}, but the bill shows "
                            f"{amount:,.2f}."
                        ),
                        expected=_money(expected.quantize(Decimal("0.01"))),
                        actual=_money(amount),
                    )
                )

    for label, field, rows_attr in (
        ("CGST", inv.cgst_amount, "cgst_amount"),
        ("SGST", inv.sgst_amount, "sgst_amount"),
        ("IGST", inv.igst_amount, "igst_amount"),
    ):
        header = _d(field)
        if header is None:
            continue
        table = _sum(getattr(r, rows_attr) for r in inv.tax_rows)
        if abs(header - table) > TOL:
            flags.append(
                Flag(
                    rule="tax_summary_mismatch",
                    severity=WARNING,
                    field_path=rows_attr,
                    message=(
                        f"{label} on the invoice is {header:,.2f} but the HSN "
                        f"table totals {table:,.2f}."
                    ),
                    expected=_money(header),
                    actual=_money(table),
                )
            )
    return flags


def _check_amount_in_words(inv) -> list[Flag]:
    """The strongest check available: the bill states its own total twice."""
    if not inv.amount_in_words or inv.grand_total is None:
        return []
    spelled = words_to_number(inv.amount_in_words)
    if spelled is None:
        return []
    grand = _d(inv.grand_total)
    if grand is None or abs(spelled - grand) <= TOL:
        return []
    return [
        Flag(
            rule="amount_in_words_mismatch",
            severity=ERROR,
            field_path="grand_total",
            message=(
                f"The bill spells out {spelled:,.2f} but the extracted grand "
                f"total is {grand:,.2f}. One of the two was misread."
            ),
            expected=_money(spelled),
            actual=_money(grand),
        )
    ]


def _check_supply_type(inv) -> list[Flag]:
    """Intra-state supply is CGST+SGST; inter-state is IGST. Never both."""
    flags: list[Flag] = []
    seller_state = inv.seller.state_code if inv.seller else None
    buyer_state = (inv.consignee or inv.buyer).state_code if (inv.consignee or inv.buyer) else None
    if not seller_state or not buyer_state:
        return flags

    intra = seller_state == buyer_state
    cgst = _sum([inv.cgst_amount]) + _sum([inv.sgst_amount])
    igst = _sum([inv.igst_amount])

    if intra and igst > TOL:
        flags.append(
            Flag(
                rule="supply_type_mismatch",
                severity=ERROR,
                field_path="igst_amount",
                message=(
                    f"Both parties are in {STATE_CODES.get(seller_state, seller_state)}, "
                    f"so the bill should carry CGST+SGST, but IGST of "
                    f"{igst:,.2f} was extracted."
                ),
            )
        )
    if not intra and cgst > TOL:
        flags.append(
            Flag(
                rule="supply_type_mismatch",
                severity=ERROR,
                field_path="cgst_amount",
                message=(
                    f"Seller is in {STATE_CODES.get(seller_state, seller_state)} and "
                    f"the goods go to {STATE_CODES.get(buyer_state, buyer_state)}, so "
                    f"the bill should carry IGST, but CGST+SGST of {cgst:,.2f} "
                    "was extracted."
                ),
            )
        )
    if intra and inv.cgst_amount is not None and inv.sgst_amount is not None:
        if abs(_d(inv.cgst_amount) - _d(inv.sgst_amount)) > TOL:
            flags.append(
                Flag(
                    rule="cgst_sgst_unequal",
                    severity=WARNING,
                    field_path="sgst_amount",
                    message=(
                        f"CGST ({_money(_d(inv.cgst_amount))}) and SGST "
                        f"({_money(_d(inv.sgst_amount))}) differ; they are always "
                        "equal on an intra-state bill."
                    ),
                )
            )
    return flags


def _check_formats(inv) -> list[Flag]:
    flags: list[Flag] = []

    round_off = _d(inv.round_off)
    if round_off is not None and abs(round_off) >= Decimal("1"):
        flags.append(
            Flag(
                rule="round_off_out_of_range",
                severity=WARNING,
                field_path="round_off",
                message=(
                    f"Round-off of {round_off:,.2f} is larger than a rounding "
                    "adjustment should ever be."
                ),
                actual=_money(round_off),
            )
        )

    for line in inv.lines:
        if not line.hsn:
            continue
        hsn = normalize_hsn(line.hsn)
        if hsn and len(hsn) not in (4, 6, 8):
            flags.append(
                Flag(
                    rule="hsn_format",
                    severity=WARNING,
                    field_path=f"lines.{line.line_no}.hsn",
                    message=f"HSN '{line.hsn}' has {len(hsn)} digits; valid codes have 4, 6 or 8.",
                    actual=line.hsn,
                )
            )

    if inv.invoice_date:
        today = date.today()
        if inv.invoice_date > today + timedelta(days=1):
            flags.append(
                Flag(
                    rule="date_in_future",
                    severity=WARNING,
                    field_path="invoice_date",
                    message=f"Invoice date {inv.invoice_date} is in the future.",
                    actual=str(inv.invoice_date),
                )
            )
        elif inv.invoice_date < date(2000, 1, 1):
            flags.append(
                Flag(
                    rule="date_implausible",
                    severity=WARNING,
                    field_path="invoice_date",
                    message=f"Invoice date {inv.invoice_date} looks misread.",
                    actual=str(inv.invoice_date),
                )
            )

    if inv.due_date and inv.invoice_date and inv.due_date < inv.invoice_date:
        flags.append(
            Flag(
                rule="due_before_invoice",
                severity=WARNING,
                field_path="due_date",
                message=f"Due date {inv.due_date} is before the invoice date {inv.invoice_date}.",
            )
        )

    if inv.irn and len(str(inv.irn)) != 64:
        flags.append(
            Flag(
                rule="irn_length",
                severity=WARNING,
                field_path="irn",
                message=f"IRN is {len(str(inv.irn))} characters; an e-invoice IRN is 64.",
            )
        )

    if inv.eway_bill_no and len(str(inv.eway_bill_no).strip()) != 12:
        flags.append(
            Flag(
                rule="eway_bill_length",
                severity=WARNING,
                field_path="eway_bill_no",
                message=(
                    f"e-Way bill number '{inv.eway_bill_no}' is not the usual "
                    "12 digits."
                ),
            )
        )
    return flags


def _check_confidence(inv, confidence: float | None) -> list[Flag]:
    if confidence is None or confidence >= 0.85:
        return []
    return [
        Flag(
            rule="low_confidence",
            severity=WARNING if confidence >= 0.6 else ERROR,
            field_path=None,
            message=(
                f"The extraction reported {confidence:.0%} confidence overall — "
                "check this bill against the original."
            ),
            actual=f"{confidence:.2f}",
        )
    ]


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


# Marks an entry the reader put in `unmapped_fields` to say it saw something on
# the page and could not file it.
NOT_CAPTURED = "not_captured"


def _check_completeness(inv) -> list[Flag]:
    """Did anything printed on the bill fail to reach the ledger?

    The arithmetic rules verify money and nothing else. A bill can reconcile
    to the paisa with its transporter, its broker or its bank details missing
    entirely, and post clean — which is exactly how a transporter-wise report
    ends up quietly short a consignment.

    So the reader records what it saw but could not file, and those gaps are
    surfaced here as warnings. They do not block the bill; they make sure a
    person is told rather than left to discover it in a report months later.
    """
    flags: list[Flag] = []

    for entry in (inv.unmapped_fields or []):
        if not isinstance(entry, dict) or entry.get("section") != NOT_CAPTURED:
            continue
        flags.append(
            Flag(
                rule="field_not_captured",
                severity=WARNING,
                field_path=entry.get("value") or None,
                message=(
                    f"The bill prints {entry.get('label', 'a value')} but it "
                    "was not read. Nothing checks this field, so it would "
                    "otherwise be missing without notice."
                ),
            )
        )

    # Structural gaps that need no knowledge of the page.
    if inv.eway_bill_no and inv.transporter_id is None:
        flags.append(
            Flag(
                rule="transporter_missing",
                severity=WARNING,
                field_path="transporter",
                message=(
                    f"e-Way bill {inv.eway_bill_no} is recorded but no "
                    "transporter was identified, so this consignment will not "
                    "appear in transporter-wise reporting."
                ),
            )
        )
    if inv.broker_name_raw and inv.broker_id is None:
        flags.append(
            Flag(
                rule="broker_unresolved",
                severity=WARNING,
                field_path="broker",
                message=(
                    f"The bill names '{inv.broker_name_raw}' as broker but it "
                    "was not matched to a party, so brokerage will not be "
                    "attributed."
                ),
            )
        )
    for line in inv.lines:
        if not (line.description or "").strip():
            flags.append(
                Flag(
                    rule="line_without_description",
                    severity=WARNING,
                    field_path=f"lines.{line.line_no}.description",
                    message=(
                        f"Line {line.line_no} carries figures but no product "
                        "name was read."
                    ),
                )
            )
    return flags


def validate_invoice(inv, *, confidence: float | None = None) -> list[Flag]:
    """Run every rule against a persisted Invoice (with relations loaded)."""
    flags: list[Flag] = []
    flags += _check_required(inv)
    flags += _check_gstins(inv)
    flags += _check_line_arithmetic(inv)
    flags += _check_totals(inv)
    flags += _check_tax_summary(inv)
    flags += _check_amount_in_words(inv)
    flags += _check_supply_type(inv)
    flags += _check_formats(inv)
    flags += _check_confidence(inv, confidence)
    flags += _check_completeness(inv)
    return flags


def worst_severity(flags: list[Flag]) -> str | None:
    if any(f.severity == ERROR for f in flags):
        return ERROR
    if any(f.severity == WARNING for f in flags):
        return WARNING
    return INFO if flags else None
