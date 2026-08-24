"""Turn ORM rows into JSON-safe dictionaries."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal


def num(value):
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return value


def iso(value):
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def party_brief(party) -> dict | None:
    if party is None:
        return None
    return {
        "id": party.id,
        "name": party.display_name or party.legal_name,
        "gstin": party.gstin,
        "state_name": party.state_name,
        "state_code": party.state_code,
        "city": party.city,
    }


def party_full(party) -> dict:
    data = party_brief(party) or {}
    data.update(
        {
            "legal_name": party.legal_name,
            "pan": party.pan,
            "fssai": party.fssai,
            "address": party.address,
            "pincode": party.pincode,
            "phone": party.phone,
            "email": party.email,
            "is_seller": party.is_seller,
            "is_buyer": party.is_buyer,
            "is_transporter": party.is_transporter,
            "is_broker": party.is_broker,
            "credit_days": party.credit_days,
            "credit_limit": num(party.credit_limit),
            "contact_person": party.contact_person,
            "notes": party.notes,
            "is_verified": party.is_verified,
            "aliases": [a.alias for a in party.aliases],
        }
    )
    return data


def line(row) -> dict:
    return {
        "id": row.id,
        "line_no": row.line_no,
        "description": row.description,
        "item_code": row.item_code,
        "item_remarks": row.item_remarks,
        "brand": row.brand,
        "hsn": row.hsn,
        "bags": num(row.bags),
        "quantity": num(row.quantity),
        "uom": row.uom,
        "rate": num(row.rate),
        "rate_uom": row.rate_uom,
        "discount_pct": num(row.discount_pct),
        "discount_amount": num(row.discount_amount),
        "taxable_amount": num(row.taxable_amount),
        "tax_rate": num(row.tax_rate),
        "cgst_amount": num(row.cgst_amount),
        "sgst_amount": num(row.sgst_amount),
        "igst_amount": num(row.igst_amount),
        "line_total": num(row.line_total),
        "product_id": row.product_id,
        "product": row.product.canonical_name if row.product else None,
    }


def charge(row) -> dict:
    return {
        "id": row.id,
        "label": row.label,
        "kind": row.kind,
        "amount": num(row.amount),
        "hsn": row.hsn,
        "tax_rate": num(row.tax_rate),
    }


def tax_row(row) -> dict:
    return {
        "id": row.id,
        "hsn": row.hsn,
        "taxable_value": num(row.taxable_value),
        "cgst_rate": num(row.cgst_rate),
        "cgst_amount": num(row.cgst_amount),
        "sgst_rate": num(row.sgst_rate),
        "sgst_amount": num(row.sgst_amount),
        "igst_rate": num(row.igst_rate),
        "igst_amount": num(row.igst_amount),
        "cess_amount": num(row.cess_amount),
        "total_tax": num(row.total_tax),
    }


def eway(row) -> dict | None:
    if row is None:
        return None
    return {
        "eway_bill_no": row.eway_bill_no,
        "generated_date": iso(row.generated_date),
        "generated_by": row.generated_by,
        "valid_upto": iso(row.valid_upto),
        "mode": row.mode,
        "approx_distance_km": row.approx_distance_km,
        "supply_type": row.supply_type,
        "transaction_type": row.transaction_type,
        "dispatch_from": row.dispatch_from,
        "ship_to": row.ship_to,
        "transporter_id_no": row.transporter_id_no,
        "transporter_name": row.transporter_name,
        "transporter_doc_no": row.transporter_doc_no,
        "vehicle_no": row.vehicle_no,
        "vehicle_from": row.vehicle_from,
        "cewb_no": row.cewb_no,
    }


def flag(row) -> dict:
    return {
        "id": row.id,
        "rule": row.rule,
        "severity": row.severity,
        "field_path": row.field_path,
        "message": row.message,
        "expected": row.expected,
        "actual": row.actual,
        "resolved": row.resolved,
    }


def invoice_brief(inv) -> dict:
    return {
        "id": inv.id,
        "document_id": inv.document_id,
        "invoice_number": inv.invoice_number,
        "invoice_date": iso(inv.invoice_date),
        "due_date": iso(inv.due_date),
        "financial_year": inv.financial_year,
        "document_type": inv.document_type,
        "seller": party_brief(inv.seller),
        "buyer": party_brief(inv.buyer),
        "transporter": party_brief(inv.transporter),
        "broker": party_brief(inv.broker) or ({"name": inv.broker_name_raw} if inv.broker_name_raw else None),
        "supply_type": inv.supply_type,
        "taxable_value": num(inv.taxable_value),
        "grand_total": num(inv.grand_total),
        "total_quantity": num(inv.total_quantity),
        "total_quantity_uom": inv.total_quantity_uom,
        "status": inv.status,
        "needs_review": inv.needs_review,
        "confidence": inv.confidence,
        "payment_status": inv.payment_status,
        "amount_paid": num(inv.amount_paid),
        "error_count": sum(1 for f in inv.flags if f.severity == "error" and not f.resolved),
        "warning_count": sum(1 for f in inv.flags if f.severity == "warning" and not f.resolved),
    }


def invoice_full(inv) -> dict:
    data = invoice_brief(inv)
    data.update(
        {
            "consignee": party_brief(inv.consignee),
            "broker_name_raw": inv.broker_name_raw,
            "irn": inv.irn,
            "ack_no": inv.ack_no,
            "ack_date": iso(inv.ack_date),
            "po_number": inv.po_number,
            "po_date": iso(inv.po_date),
            "delivery_note": inv.delivery_note,
            "delivery_note_date": iso(inv.delivery_note_date),
            "place_of_supply": inv.place_of_supply,
            "payment_terms": inv.payment_terms,
            "currency": inv.currency,
            "total_bags": num(inv.total_bags),
            "subtotal": num(inv.subtotal),
            "discount_total": num(inv.discount_total),
            "cgst_amount": num(inv.cgst_amount),
            "sgst_amount": num(inv.sgst_amount),
            "igst_amount": num(inv.igst_amount),
            "cess_amount": num(inv.cess_amount),
            "tcs_amount": num(inv.tcs_amount),
            "other_charges": num(inv.other_charges),
            "round_off": num(inv.round_off),
            "amount_in_words": inv.amount_in_words,
            "eway_bill_no": inv.eway_bill_no,
            "vehicle_no": inv.vehicle_no,
            "bank_name": inv.bank_name,
            "bank_account_no": inv.bank_account_no,
            "bank_ifsc": inv.bank_ifsc,
            "bank_branch": inv.bank_branch,
            "remarks": inv.remarks,
            "terms": inv.terms,
            "vendor_format_hint": inv.vendor_format_hint,
            "unmapped_fields": inv.unmapped_fields or [],
            "lines": [line(l) for l in inv.lines],
            "charges": [charge(c) for c in inv.charges],
            "tax_rows": [tax_row(t) for t in inv.tax_rows],
            "eway_bill": eway(inv.eway_bill),
            "flags": [flag(f) for f in inv.flags],
            "created_at": iso(inv.created_at),
            "reviewed_by": inv.reviewed_by,
            "reviewed_at": iso(inv.reviewed_at),
        }
    )
    return data


def document(doc, *, invoice_id: int | None = None) -> dict:
    return {
        "id": doc.id,
        "filename": doc.original_filename,
        "sha256": doc.sha256,
        "mime_type": doc.mime_type,
        "size_bytes": doc.size_bytes,
        "page_count": doc.page_count,
        "status": doc.status,
        "error_message": doc.error_message,
        "text_quality": doc.text_quality,
        "extraction_route": doc.extraction_route,
        "producer": doc.producer,
        "uploaded_at": iso(doc.created_at),
        "invoice_id": invoice_id if invoice_id is not None else (
            doc.invoices[0].id if doc.invoices else None
        ),
    }
