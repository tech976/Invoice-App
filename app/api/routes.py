"""JSON API."""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.api import serializers as ser
from app.config import settings
from app.db import get_db
from app.extraction.normalize import (
    clean_gstin,
    clean_text,
    financial_year,
    normalize_hsn,
    normalize_uom,
    parse_amount,
    parse_date,
    parse_percent,
)
from app.extraction.pipeline import revalidate
from app.ingest.storage import store_bytes
from app.models import (
    BrokerageEntry,
    Correction,
    Document,
    Invoice,
    InvoiceLine,
    Party,
    Product,
    ValidationFlag,
)
from app.worker import enqueue

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


def _invoice_query():
    return select(Invoice).options(
        selectinload(Invoice.seller),
        selectinload(Invoice.buyer),
        selectinload(Invoice.consignee),
        selectinload(Invoice.transporter),
        selectinload(Invoice.broker),
        selectinload(Invoice.lines).selectinload(InvoiceLine.product),
        selectinload(Invoice.charges),
        selectinload(Invoice.tax_rows),
        selectinload(Invoice.eway_bill),
        selectinload(Invoice.flags),
    )


def _get_invoice(db: Session, invoice_id: int) -> Invoice:
    inv = db.scalar(_invoice_query().where(Invoice.id == invoice_id))
    if inv is None:
        raise HTTPException(404, f"Invoice {invoice_id} not found")
    return inv


# ==========================================================================
# Upload & documents
# ==========================================================================


@router.post("/documents/upload")
async def upload_documents(
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
) -> dict:
    """Accept one or more bills, queue each for extraction."""
    results = []
    max_bytes = settings.max_upload_mb * 1024 * 1024

    for upload in files:
        data = await upload.read()
        if not data:
            results.append({"filename": upload.filename, "status": "error",
                            "message": "The file is empty."})
            continue
        if len(data) > max_bytes:
            results.append({"filename": upload.filename, "status": "error",
                            "message": f"Larger than the {settings.max_upload_mb} MB limit."})
            continue

        try:
            digest, stored_path, mime = store_bytes(data, upload.filename or "upload.pdf")
        except ValueError as exc:
            results.append({"filename": upload.filename, "status": "error", "message": str(exc)})
            continue

        existing = db.scalar(select(Document).where(Document.sha256 == digest))
        if existing is not None:
            results.append({
                "filename": upload.filename,
                "status": "duplicate",
                "document_id": existing.id,
                "invoice_id": existing.invoices[0].id if existing.invoices else None,
                "message": (
                    f"Identical file already uploaded on "
                    f"{existing.created_at:%d %b %Y} as '{existing.original_filename}'."
                ),
            })
            continue

        doc = Document(
            sha256=digest,
            original_filename=upload.filename or stored_path.name,
            stored_path=str(stored_path),
            mime_type=mime,
            size_bytes=len(data),
            status="queued",
        )
        db.add(doc)
        db.flush()
        enqueue(db, doc.id)
        results.append({"filename": upload.filename, "status": "queued", "document_id": doc.id})

    db.commit()
    queued = sum(1 for r in results if r["status"] == "queued")
    return {"queued": queued, "results": results}


@router.get("/documents")
def list_documents(
    status: str | None = None,
    limit: int = Query(100, le=500),
    offset: int = 0,
    db: Session = Depends(get_db),
) -> dict:
    stmt = select(Document).options(selectinload(Document.invoices))
    if status:
        stmt = stmt.where(Document.status == status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    docs = db.scalars(
        stmt.order_by(Document.created_at.desc()).limit(limit).offset(offset)
    ).all()
    return {"total": total, "documents": [ser.document(d) for d in docs]}


@router.get("/documents/{document_id}")
def get_document(document_id: int, db: Session = Depends(get_db)) -> dict:
    doc = db.get(Document, document_id)
    if doc is None:
        raise HTTPException(404, "Document not found")
    data = ser.document(doc)
    data["pages"] = [
        {"page_no": p.page_no, "text_quality": p.text_quality, "has_image": bool(p.image_path)}
        for p in doc.pages
    ]
    data["runs"] = [
        {
            "id": r.id, "model": r.model, "status": r.status,
            "pass_type": r.pass_type, "duration_ms": r.duration_ms,
            "input_tokens": r.input_tokens, "output_tokens": r.output_tokens,
            "error": r.error_message, "started_at": ser.iso(r.started_at),
        }
        for r in doc.runs
    ]
    return data


@router.get("/documents/{document_id}/file")
def get_document_file(document_id: int, db: Session = Depends(get_db)):
    """Serve the original uploaded bill."""
    doc = db.get(Document, document_id)
    if doc is None:
        raise HTTPException(404, "Document not found")
    path = Path(doc.stored_path)
    if not path.exists():
        raise HTTPException(410, "The stored file is no longer on disk.")
    return FileResponse(
        path,
        media_type=doc.mime_type,
        filename=doc.original_filename,
        content_disposition_type="inline",
    )


@router.get("/documents/{document_id}/page/{page_no}")
def get_document_page(document_id: int, page_no: int, db: Session = Depends(get_db)):
    doc = db.get(Document, document_id)
    if doc is None:
        raise HTTPException(404, "Document not found")
    page = next((p for p in doc.pages if p.page_no == page_no), None)
    if page is None or not page.image_path or not Path(page.image_path).exists():
        raise HTTPException(404, "Page image not available")
    return FileResponse(page.image_path, media_type="image/png")


@router.post("/documents/{document_id}/reprocess")
def reprocess_document(document_id: int, db: Session = Depends(get_db)) -> dict:
    """Read the bill again — after a prompt change, or a failed run."""
    doc = db.get(Document, document_id)
    if doc is None:
        raise HTTPException(404, "Document not found")
    confirmed = [i for i in doc.invoices if i.status == "confirmed"]
    if confirmed:
        raise HTTPException(
            409,
            f"Invoice #{confirmed[0].id} from this document is already confirmed. "
            "Reopen it for review before re-reading the bill.",
        )
    doc.status = "queued"
    doc.error_message = None
    enqueue(db, doc.id)
    db.commit()
    return {"status": "queued", "document_id": doc.id}


@router.delete("/documents/{document_id}")
def delete_document(document_id: int, db: Session = Depends(get_db)) -> dict:
    doc = db.get(Document, document_id)
    if doc is None:
        raise HTTPException(404, "Document not found")
    db.delete(doc)
    db.commit()
    # The stored file itself is kept: it is the evidence behind every figure
    # that was ever posted from it.
    return {"deleted": document_id}


# ==========================================================================
# Invoices
# ==========================================================================


@router.get("/invoices")
def list_invoices(
    q: str | None = None,
    seller_id: int | None = None,
    buyer_id: int | None = None,
    transporter_id: int | None = None,
    broker_id: int | None = None,
    status: str | None = None,
    payment_status: str | None = None,
    financial_year_: str | None = Query(None, alias="financial_year"),
    date_from: date | None = None,
    date_to: date | None = None,
    needs_review: bool | None = None,
    sort: str = "date_desc",
    limit: int = Query(100, le=1000),
    offset: int = 0,
    db: Session = Depends(get_db),
) -> dict:
    stmt = _invoice_query()

    if seller_id:
        stmt = stmt.where(Invoice.seller_id == seller_id)
    if buyer_id:
        stmt = stmt.where(Invoice.buyer_id == buyer_id)
    if transporter_id:
        stmt = stmt.where(Invoice.transporter_id == transporter_id)
    if broker_id:
        stmt = stmt.where(Invoice.broker_id == broker_id)
    if status:
        stmt = stmt.where(Invoice.status == status)
    if payment_status:
        stmt = stmt.where(Invoice.payment_status == payment_status)
    if financial_year_:
        stmt = stmt.where(Invoice.financial_year == financial_year_)
    if date_from:
        stmt = stmt.where(Invoice.invoice_date >= date_from)
    if date_to:
        stmt = stmt.where(Invoice.invoice_date <= date_to)
    if needs_review is not None:
        stmt = stmt.where(Invoice.needs_review.is_(needs_review))
    if q:
        like = f"%{q.strip()}%"
        seller = select(Party.id).where(Party.legal_name.ilike(like))
        stmt = stmt.where(
            or_(
                Invoice.invoice_number.ilike(like),
                Invoice.eway_bill_no.ilike(like),
                Invoice.vehicle_no.ilike(like),
                Invoice.broker_name_raw.ilike(like),
                Invoice.seller_id.in_(seller),
                Invoice.buyer_id.in_(seller),
            )
        )

    orders = {
        "date_desc": Invoice.invoice_date.desc().nullslast(),
        "date_asc": Invoice.invoice_date.asc().nullsfirst(),
        "amount_desc": Invoice.grand_total.desc().nullslast(),
        "amount_asc": Invoice.grand_total.asc().nullsfirst(),
        "recent": Invoice.created_at.desc(),
    }
    stmt = stmt.order_by(orders.get(sort, orders["date_desc"]), Invoice.id.desc())

    count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
    total = db.scalar(count_stmt) or 0
    rows = db.scalars(stmt.limit(limit).offset(offset)).unique().all()

    totals = {
        "grand_total": sum(float(r.grand_total or 0) for r in rows),
        "taxable_value": sum(float(r.taxable_value or 0) for r in rows),
    }
    return {
        "total": total,
        "shown": len(rows),
        "page_totals": totals,
        "invoices": [ser.invoice_brief(r) for r in rows],
    }


@router.get("/invoices/{invoice_id}")
def get_invoice(invoice_id: int, db: Session = Depends(get_db)) -> dict:
    inv = _get_invoice(db, invoice_id)
    data = ser.invoice_full(inv)
    data["document"] = ser.document(inv.document, invoice_id=inv.id)
    entry = db.scalar(select(BrokerageEntry).where(BrokerageEntry.invoice_id == inv.id))
    data["brokerage"] = (
        {
            "amount": ser.num(entry.amount),
            "rate_pct": ser.num(entry.rate_pct),
            "basis_amount": ser.num(entry.basis_amount),
            "payable_by": entry.payable_by,
            "status": entry.status,
        }
        if entry
        else None
    )
    return data


# Fields a human may correct, and how each is cleaned on the way in.
INVOICE_EDITABLE: dict[str, Any] = {
    "invoice_number": clean_text,
    "invoice_date": parse_date,
    "due_date": parse_date,
    "irn": clean_text,
    "ack_no": clean_text,
    "ack_date": parse_date,
    "po_number": clean_text,
    "po_date": parse_date,
    "delivery_note": clean_text,
    "place_of_supply": clean_text,
    "payment_terms": clean_text,
    "document_type": clean_text,
    "subtotal": parse_amount,
    "discount_total": parse_amount,
    "taxable_value": parse_amount,
    "cgst_amount": parse_amount,
    "sgst_amount": parse_amount,
    "igst_amount": parse_amount,
    "cess_amount": parse_amount,
    "tcs_amount": parse_amount,
    "other_charges": parse_amount,
    "round_off": parse_amount,
    "grand_total": parse_amount,
    "total_quantity": parse_amount,
    "total_bags": parse_amount,
    "total_quantity_uom": normalize_uom,
    "amount_in_words": clean_text,
    "eway_bill_no": clean_text,
    "vehicle_no": clean_text,
    "bank_name": clean_text,
    "bank_account_no": clean_text,
    "bank_ifsc": clean_text,
    "bank_branch": clean_text,
    "remarks": clean_text,
    "broker_name_raw": clean_text,
}

LINE_EDITABLE: dict[str, Any] = {
    "description": clean_text,
    "item_code": clean_text,
    "item_remarks": clean_text,
    "brand": clean_text,
    "hsn": normalize_hsn,
    "bags": parse_amount,
    "quantity": parse_amount,
    "uom": normalize_uom,
    "rate": parse_amount,
    "discount_pct": parse_percent,
    "discount_amount": parse_amount,
    "taxable_amount": parse_amount,
    "tax_rate": parse_percent,
    "cgst_amount": parse_amount,
    "sgst_amount": parse_amount,
    "igst_amount": parse_amount,
    "line_total": parse_amount,
}


def _apply_edits(
    db: Session,
    invoice: Invoice,
    target,
    changes: dict,
    allowed: dict,
    *,
    entity: str,
    entity_id: int | None,
    user: str | None,
) -> list[str]:
    """Apply edits, recording each as a Correction.

    The correction log is the feedback loop: when the same field on the same
    vendor format keeps being retyped the same way, that is a prompt bug worth
    fixing rather than a chore to repeat forever.
    """
    applied = []
    for field, raw in changes.items():
        if field not in allowed:
            continue
        cleaned = allowed[field](raw) if raw not in (None, "") else None
        old = getattr(target, field)
        if str(old) == str(cleaned):
            continue
        setattr(target, field, cleaned)
        db.add(
            Correction(
                invoice_id=invoice.id,
                entity=entity,
                entity_id=entity_id,
                field_path=field,
                old_value=None if old is None else str(old),
                new_value=None if cleaned is None else str(cleaned),
                vendor_format_hint=invoice.vendor_format_hint,
                corrected_by=user,
            )
        )
        applied.append(field)
    return applied


@router.patch("/invoices/{invoice_id}")
def update_invoice(
    invoice_id: int,
    payload: dict = Body(...),
    db: Session = Depends(get_db),
) -> dict:
    inv = _get_invoice(db, invoice_id)
    user = payload.pop("_user", None)
    changed = _apply_edits(
        db, inv, inv, payload, INVOICE_EDITABLE,
        entity="invoice", entity_id=inv.id, user=user,
    )
    if "invoice_date" in changed:
        inv.financial_year = financial_year(inv.invoice_date)
    db.flush()
    revalidate(db, inv)
    db.commit()
    db.refresh(inv)
    return {"updated": changed, "invoice": ser.invoice_full(_get_invoice(db, invoice_id))}


@router.patch("/invoices/{invoice_id}/lines/{line_id}")
def update_line(
    invoice_id: int,
    line_id: int,
    payload: dict = Body(...),
    db: Session = Depends(get_db),
) -> dict:
    inv = _get_invoice(db, invoice_id)
    row = next((l for l in inv.lines if l.id == line_id), None)
    if row is None:
        raise HTTPException(404, "Line not found on this invoice")
    user = payload.pop("_user", None)
    changed = _apply_edits(
        db, inv, row, payload, LINE_EDITABLE,
        entity="line", entity_id=row.id, user=user,
    )
    db.flush()
    revalidate(db, inv)
    db.commit()
    return {"updated": changed, "invoice": ser.invoice_full(_get_invoice(db, invoice_id))}


@router.post("/invoices/{invoice_id}/confirm")
def confirm_invoice(
    invoice_id: int,
    payload: dict = Body(default={}),
    db: Session = Depends(get_db),
) -> dict:
    """Mark an invoice checked. Blocked while an error flag is open."""
    inv = _get_invoice(db, invoice_id)
    open_errors = [f for f in inv.flags if f.severity == "error" and not f.resolved]
    if open_errors and not payload.get("force"):
        raise HTTPException(
            409,
            {
                "message": "This invoice still has unresolved errors.",
                "errors": [f.message for f in open_errors],
            },
        )
    inv.status = "confirmed"
    inv.needs_review = False
    inv.reviewed_by = payload.get("user")
    inv.reviewed_at = datetime.now(timezone.utc)
    inv.document.status = "confirmed"
    if inv.seller:
        inv.seller.is_verified = True
    db.commit()
    return {"status": "confirmed", "invoice_id": inv.id}


@router.post("/invoices/{invoice_id}/reopen")
def reopen_invoice(invoice_id: int, db: Session = Depends(get_db)) -> dict:
    inv = _get_invoice(db, invoice_id)
    inv.status = "needs_review"
    inv.needs_review = True
    inv.document.status = "needs_review"
    db.commit()
    return {"status": "needs_review", "invoice_id": inv.id}


@router.post("/invoices/{invoice_id}/flags/{flag_id}/resolve")
def resolve_flag(
    invoice_id: int,
    flag_id: int,
    payload: dict = Body(default={}),
    db: Session = Depends(get_db),
) -> dict:
    """Accept a flag as a known quirk of this bill rather than an error."""
    flag = db.get(ValidationFlag, flag_id)
    if flag is None or flag.invoice_id != invoice_id:
        raise HTTPException(404, "Flag not found on this invoice")
    flag.resolved = True
    flag.resolved_by = payload.get("user")
    flag.resolved_at = datetime.now(timezone.utc)

    inv = _get_invoice(db, invoice_id)
    open_any = [f for f in inv.flags if not f.resolved and f.id != flag_id]
    if inv.status != "confirmed":
        inv.needs_review = bool(open_any)
        inv.status = "needs_review" if open_any else "extracted"
    db.commit()
    return {"resolved": flag_id, "remaining": len(open_any)}


@router.post("/invoices/{invoice_id}/revalidate")
def revalidate_invoice(invoice_id: int, db: Session = Depends(get_db)) -> dict:
    inv = _get_invoice(db, invoice_id)
    flags = revalidate(db, inv)
    db.commit()
    return {"flags": [ser.flag(f) for f in flags]}


@router.delete("/invoices/{invoice_id}")
def delete_invoice(invoice_id: int, db: Session = Depends(get_db)) -> dict:
    inv = _get_invoice(db, invoice_id)
    doc_id = inv.document_id
    db.delete(inv)
    doc = db.get(Document, doc_id)
    if doc:
        doc.status = "uploaded"
    db.commit()
    return {"deleted": invoice_id}
