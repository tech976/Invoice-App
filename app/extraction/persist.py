"""Write an `ExtractedInvoice` into the ledger tables.

Everything the model returned passes through `normalize` on the way in, so a
value typed by a human on the review screen and a value read off a PDF are
stored in exactly the same shape.
"""
from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.extraction.normalize import (
    clean_gstin,
    clean_text,
    financial_year,
    normalize_hsn,
    normalize_uom,
    normalize_vehicle_no,
    parse_amount,
    parse_date,
    parse_percent,
)
from app.matching.parties import resolve_broker, resolve_party, resolve_transporter
from app.matching.products import resolve_product
from app.models import (
    Document,
    EwayBill,
    Invoice,
    InvoiceCharge,
    InvoiceLine,
    InvoiceTaxRow,
)
from app.schemas import ExtractedInvoice

log = logging.getLogger(__name__)

_DAYS_RE = re.compile(r"(\d+)\s*(?:days?|d\b)", re.I)


def derive_due_date(invoice_date: date | None, payment_terms: str | None) -> date | None:
    """'7 Days' on a 21-Jul bill means payment is due 28-Jul."""
    if not invoice_date or not payment_terms:
        return None
    match = _DAYS_RE.search(str(payment_terms))
    if not match:
        return None
    try:
        return invoice_date + timedelta(days=int(match.group(1)))
    except (ValueError, OverflowError):
        return None


def derive_supply_type(seller_state: str | None, buyer_state: str | None) -> str | None:
    if not seller_state or not buyer_state:
        return None
    return "intra" if seller_state == buyer_state else "inter"


def find_duplicate(
    db: Session, *, seller_id: int | None, invoice_number: str | None, fy: str | None
) -> Invoice | None:
    """One invoice number per seller per financial year — that is the rule
    sellers themselves follow, so a second hit is a genuine duplicate."""
    if not seller_id or not invoice_number:
        return None
    return db.scalar(
        select(Invoice).where(
            Invoice.seller_id == seller_id,
            Invoice.invoice_number == invoice_number,
            Invoice.financial_year == fy,
        )
    )


def persist_invoice(
    db: Session,
    *,
    document: Document,
    extracted: ExtractedInvoice,
    extraction_run_id: int | None = None,
) -> Invoice:
    """Create the Invoice and all its children. Caller commits."""
    seller = resolve_party(db, extracted.seller, role="seller")
    buyer = resolve_party(db, extracted.buyer, role="buyer")

    consignee = None
    if extracted.consignee:
        consignee = resolve_party(db, extracted.consignee, role="buyer")
        # A bill that repeats the buyer block as the consignee tells us
        # nothing; keep the link only when it is genuinely a third address.
        if consignee is not None and buyer is not None and consignee.id == buyer.id:
            consignee = None

    broker = resolve_broker(db, extracted.broker_name)

    transporter = None
    ewb = extracted.eway_bill
    if ewb and (ewb.transporter_name or ewb.transporter_id):
        transporter = resolve_transporter(
            db, name=ewb.transporter_name, transporter_id=ewb.transporter_id
        )

    invoice_date = parse_date(extracted.invoice_date)
    fy = financial_year(invoice_date)
    due_date = parse_date(extracted.due_date) or derive_due_date(
        invoice_date, extracted.payment_terms
    )

    buyer_state = (consignee or buyer).state_code if (consignee or buyer) else None
    supply_type = derive_supply_type(seller.state_code if seller else None, buyer_state)

    invoice = Invoice(
        document_id=document.id,
        extraction_run_id=extraction_run_id,
        document_type=extracted.document_type,
        invoice_number=clean_text(extracted.invoice_number),
        invoice_date=invoice_date,
        due_date=due_date,
        financial_year=fy,
        irn=clean_text(extracted.irn),
        ack_no=clean_text(extracted.ack_no),
        ack_date=parse_date(extracted.ack_date),
        po_number=clean_text(extracted.po_number),
        po_date=parse_date(extracted.po_date),
        delivery_note=clean_text(extracted.delivery_note),
        delivery_note_date=parse_date(extracted.delivery_note_date),
        seller_id=seller.id if seller else None,
        buyer_id=buyer.id if buyer else None,
        consignee_id=consignee.id if consignee else None,
        transporter_id=transporter.id if transporter else None,
        broker_id=broker.id if broker else None,
        broker_name_raw=clean_text(extracted.broker_name),
        place_of_supply=clean_text(extracted.place_of_supply),
        supply_type=supply_type,
        payment_terms=clean_text(extracted.payment_terms),
        currency=(extracted.currency or "INR")[:3].upper(),
        total_quantity=parse_amount(extracted.total_quantity),
        total_quantity_uom=normalize_uom(extracted.total_quantity_uom),
        total_bags=parse_amount(extracted.total_bags),
        subtotal=parse_amount(extracted.subtotal),
        discount_total=parse_amount(extracted.discount_total),
        taxable_value=parse_amount(extracted.taxable_value),
        cgst_amount=parse_amount(extracted.cgst_amount),
        sgst_amount=parse_amount(extracted.sgst_amount),
        igst_amount=parse_amount(extracted.igst_amount),
        cess_amount=parse_amount(extracted.cess_amount),
        tcs_amount=parse_amount(extracted.tcs_amount),
        other_charges=parse_amount(extracted.other_charges),
        round_off=parse_amount(extracted.round_off),
        grand_total=parse_amount(extracted.grand_total),
        amount_in_words=clean_text(extracted.amount_in_words),
        remarks=clean_text(extracted.remarks),
        terms=clean_text(extracted.terms),
        confidence=extracted.overall_confidence,
        vendor_format_hint=clean_text(extracted.vendor_format_hint),
        unmapped_fields=[u.model_dump() for u in extracted.unmapped_fields] or None,
        status="extracted",
        needs_review=True,
    )

    if extracted.bank:
        invoice.bank_name = clean_text(extracted.bank.bank_name)
        invoice.bank_account_no = clean_text(extracted.bank.account_number)
        invoice.bank_ifsc = clean_text(extracted.bank.ifsc)
        invoice.bank_branch = clean_text(extracted.bank.branch)

    if ewb:
        invoice.eway_bill_no = clean_text(ewb.eway_bill_no)
        invoice.vehicle_no = normalize_vehicle_no(ewb.vehicle_no)

    db.add(invoice)
    db.flush()

    _add_lines(db, invoice, extracted, seller_id=seller.id if seller else None)
    _add_charges(db, invoice, extracted)
    _add_tax_rows(db, invoice, extracted)
    if ewb:
        _add_eway(db, invoice, extracted)

    db.flush()
    _fill_derived_totals(invoice)
    return invoice


def _add_lines(db: Session, invoice: Invoice, extracted: ExtractedInvoice, *, seller_id) -> None:
    for idx, raw in enumerate(extracted.lines, start=1):
        tax_rate = parse_percent(raw.tax_rate)
        product = resolve_product(
            db,
            description=raw.description,
            item_remarks=raw.item_remarks,
            hsn=raw.hsn,
            uom=raw.uom,
            tax_rate=tax_rate,
            seller_id=seller_id,
        )
        # Appended through the relationship, not db.add(invoice_id=...): the
        # collection has to be populated for _fill_derived_totals to see it.
        invoice.lines.append(
            InvoiceLine(
                line_no=raw.line_no or idx,
                description=clean_text(raw.description),
                item_code=clean_text(raw.item_code),
                item_remarks=clean_text(raw.item_remarks),
                brand=clean_text(raw.brand),
                hsn=normalize_hsn(raw.hsn),
                bags=parse_amount(raw.bags),
                quantity=parse_amount(raw.quantity),
                uom=normalize_uom(raw.uom),
                rate=parse_amount(raw.rate),
                rate_uom=normalize_uom(raw.rate_uom) or normalize_uom(raw.uom),
                discount_pct=parse_percent(raw.discount_pct),
                discount_amount=parse_amount(raw.discount_amount),
                taxable_amount=parse_amount(raw.taxable_amount),
                tax_rate=tax_rate,
                cgst_amount=parse_amount(raw.cgst_amount),
                sgst_amount=parse_amount(raw.sgst_amount),
                igst_amount=parse_amount(raw.igst_amount),
                cess_amount=parse_amount(raw.cess_amount),
                line_total=parse_amount(raw.line_total),
                product_id=product.id if product else None,
            )
        )


def _add_charges(db: Session, invoice: Invoice, extracted: ExtractedInvoice) -> None:
    for raw in extracted.charges:
        amount = parse_amount(raw.amount)
        if amount is None:
            continue
        # A discount is a deduction whatever sign the bill printed it with.
        if raw.kind == "discount" and amount > 0:
            amount = -amount
        invoice.charges.append(
            InvoiceCharge(
                label=clean_text(raw.label) or raw.kind,
                kind=raw.kind,
                amount=amount,
                hsn=normalize_hsn(raw.hsn),
                tax_rate=parse_percent(raw.tax_rate),
            )
        )


def _add_tax_rows(db: Session, invoice: Invoice, extracted: ExtractedInvoice) -> None:
    for raw in extracted.tax_summary:
        invoice.tax_rows.append(
            InvoiceTaxRow(
                hsn=normalize_hsn(raw.hsn),
                taxable_value=parse_amount(raw.taxable_value),
                cgst_rate=parse_percent(raw.cgst_rate),
                cgst_amount=parse_amount(raw.cgst_amount),
                sgst_rate=parse_percent(raw.sgst_rate),
                sgst_amount=parse_amount(raw.sgst_amount),
                igst_rate=parse_percent(raw.igst_rate),
                igst_amount=parse_amount(raw.igst_amount),
                cess_amount=parse_amount(raw.cess_amount),
                total_tax=parse_amount(raw.total_tax),
            )
        )


def _add_eway(db: Session, invoice: Invoice, extracted: ExtractedInvoice) -> None:
    raw = extracted.eway_bill
    invoice.eway_bill = (
        EwayBill(
            eway_bill_no=clean_text(raw.eway_bill_no),
            generated_date=parse_date(raw.generated_date),
            generated_by=clean_gstin(raw.generated_by),
            valid_upto=parse_date(raw.valid_upto),
            mode=clean_text(raw.mode),
            approx_distance_km=float(parse_amount(raw.approx_distance_km) or 0) or None,
            supply_type=clean_text(raw.supply_type),
            transaction_type=clean_text(raw.transaction_type),
            dispatch_from=clean_text(raw.dispatch_from),
            ship_to=clean_text(raw.ship_to),
            transporter_id_no=clean_gstin(raw.transporter_id),
            transporter_name=clean_text(raw.transporter_name),
            transporter_doc_no=clean_text(raw.transporter_doc_no),
            transporter_doc_date=parse_date(raw.transporter_doc_date),
            vehicle_no=normalize_vehicle_no(raw.vehicle_no),
            vehicle_from=clean_text(raw.vehicle_from),
            cewb_no=clean_text(raw.cewb_no),
        )
    )


def _fill_derived_totals(invoice: Invoice) -> None:
    """Fill totals the bill did not print, from the parts that it did.

    Only ever fills blanks — a printed figure is never overwritten by a
    computed one, because the printed figure is what the parties agreed to.
    """
    lines = invoice.lines
    if invoice.subtotal is None and lines:
        values = [Decimal(str(l.taxable_amount)) for l in lines if l.taxable_amount is not None]
        if values:
            invoice.subtotal = sum(values)

    if invoice.taxable_value is None and invoice.subtotal is not None:
        discounts = sum(
            Decimal(str(c.amount)) for c in invoice.charges if c.kind == "discount"
        )
        invoice.taxable_value = Decimal(str(invoice.subtotal)) + discounts

    if invoice.other_charges is None and invoice.charges:
        additions = sum(
            Decimal(str(c.amount))
            for c in invoice.charges
            if c.kind not in ("discount", "round_off", "tcs", "tds")
        )
        if additions:
            invoice.other_charges = additions

    if invoice.round_off is None:
        for charge in invoice.charges:
            if charge.kind == "round_off":
                invoice.round_off = charge.amount
                break

    if invoice.total_quantity is None and lines:
        quantities = [Decimal(str(l.quantity)) for l in lines if l.quantity is not None]
        if quantities:
            invoice.total_quantity = sum(quantities)
            uoms = {l.uom for l in lines if l.uom}
            if len(uoms) == 1:
                invoice.total_quantity_uom = uoms.pop()

    if invoice.total_bags is None and lines:
        bags = [Decimal(str(l.bags)) for l in lines if l.bags is not None]
        if bags:
            invoice.total_bags = sum(bags)
