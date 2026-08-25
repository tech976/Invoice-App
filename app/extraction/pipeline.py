"""End-to-end processing of one uploaded bill.

    stored file
        -> text layer + quality score
        -> rendered pages (+ OCR when the text layer is junk)
        -> route decision
        -> Claude structured extraction
        -> normalise + resolve parties/products -> ledger rows
        -> deterministic validation -> review flags
        -> brokerage accrual

Nothing here is vendor-specific. A new bill format changes which route is
taken and what the model reads, not the code path.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.business.brokerage import compute_brokerage
from app.extraction.crosscheck import compare_readings
from app.config import settings
from app.extraction.local import reader as local_reader
from app.extraction.ocr import ocr_image
from app.extraction.pdf_text import extract_pdf_text, score_text
from app.extraction.persist import find_duplicate, persist_invoice
from app.extraction.rasterize import normalize_image, render_pdf_pages
from app.ingest.storage import pages_dir_for
from app.models import (
    Document, DocumentPage, ExtractionRun, Invoice, Party, ValidationFlag,
)
from app.validation.rules import ERROR, WARNING, validate_invoice

log = logging.getLogger(__name__)

ROUTE_TEXT = "text_layer"
ROUTE_OCR = "ocr_vision"
ROUTE_IMAGE = "image"


class PipelineError(RuntimeError):
    """A failure outside the model call. Not worth retrying by default."""

    retryable = False


# --------------------------------------------------------------------------
# Stage 1 — read the file
# --------------------------------------------------------------------------


def prepare_document(db: Session, document: Document) -> dict:
    """Render pages, pull text, decide the route. Idempotent."""
    path = Path(document.stored_path)
    if not path.exists():
        raise PipelineError(f"Stored file is missing: {path}")

    pages_dir = pages_dir_for(document.sha256)
    is_pdf = document.mime_type == "application/pdf"

    text_layer = ""
    page_texts: dict[int, str] = {}
    quality = 0.0

    if is_pdf:
        pdf_text = extract_pdf_text(path)
        document.producer = pdf_text.producer
        document.page_count = pdf_text.page_count
        quality = pdf_text.quality
        text_layer = pdf_text.full_text
        page_texts = {p.page_no: p.text for p in pdf_text.pages}
        images = render_pdf_pages(path, pages_dir)
        route = ROUTE_TEXT if quality >= settings.text_quality_threshold else ROUTE_OCR
    else:
        images = [normalize_image(path, pages_dir)]
        document.page_count = 1
        route = ROUTE_IMAGE

    # OCR only when the text layer cannot be trusted — it is the slow step.
    ocr_texts: dict[int, str] = {}
    if route in (ROUTE_OCR, ROUTE_IMAGE):
        for idx, img in enumerate(images, start=1):
            ocr_texts[idx] = ocr_image(img)
        combined = "\n".join(ocr_texts.values())
        ocr_quality = score_text(combined)
        log.info(
            "%s: text layer %.2f unusable, OCR recovered %.2f",
            document.original_filename, quality, ocr_quality,
        )

    _save_pages(db, document, images, page_texts, ocr_texts)

    document.text_quality = quality
    document.extraction_route = route

    return {
        "path": path,
        "route": route,
        "images": images,
        "text_layer": text_layer,
        "ocr_text": "\n\n".join(
            f"--- page {n} ---\n{t}" for n, t in sorted(ocr_texts.items()) if t.strip()
        ),
        "quality": quality,
        "page_count": document.page_count or len(images) or 1,
    }


def _save_pages(
    db: Session,
    document: Document,
    images: list[Path],
    page_texts: dict[int, str],
    ocr_texts: dict[int, str],
) -> None:
    existing = {p.page_no: p for p in document.pages}
    total = max(len(images), len(page_texts), 1)
    for page_no in range(1, total + 1):
        image = images[page_no - 1] if page_no <= len(images) else None
        text = page_texts.get(page_no, "")
        page = existing.get(page_no)
        if page is None:
            page = DocumentPage(document_id=document.id, page_no=page_no)
            db.add(page)
        page.image_path = str(image) if image else page.image_path
        page.text_layer = text or page.text_layer
        page.ocr_text = ocr_texts.get(page_no) or page.ocr_text
        page.text_quality = score_text(text) if text else page.text_quality
    db.flush()


# --------------------------------------------------------------------------
# Stage 2 — read the bill
# --------------------------------------------------------------------------


def run_extraction(
    db: Session,
    document: Document,
    prepared: dict,
    *,
    model: str | None = None,
    pass_type: str = "primary",
) -> tuple[ExtractionRun, object]:
    backend = settings.extraction_backend.strip().lower()
    local = backend == "local" and pass_type == "primary"
    model = "local-text-layer" if local else (model or settings.extraction_model)
    run = ExtractionRun(
        document_id=document.id,
        engine="local" if local else "claude",
        model=model,
        pass_type=pass_type,
        status="running",
    )
    db.add(run)
    db.flush()

    if local:
        reader = local_reader.extract_invoice
    else:
        # Imported only on the branch that needs it, so a local deployment
        # never loads the API client at all.
        from app.extraction import llm

        reader = llm.extract_invoice
    try:
        result = reader(
            model=None if local else model,
            doc_path=prepared["path"],
            mime_type=document.mime_type,
            page_images=prepared["images"],
            text_layer=prepared["text_layer"],
            ocr_text=prepared["ocr_text"],
            text_quality=prepared["quality"],
            route=prepared["route"],
            page_count=prepared["page_count"],
        )
    except Exception as exc:
        run.status = "failed"
        run.error_message = str(exc)[:2000]
        run.finished_at = datetime.now(timezone.utc)
        db.flush()
        raise

    run.status = "done"
    run.model = result.model
    run.prompt_version = result.prompt_version
    run.input_tokens = result.input_tokens
    run.output_tokens = result.output_tokens
    run.duration_ms = result.duration_ms
    run.raw_output = result.raw
    run.finished_at = datetime.now(timezone.utc)
    db.flush()
    return run, result.invoice


# --------------------------------------------------------------------------
# Stage 3 — validate
# --------------------------------------------------------------------------


def revalidate(db: Session, invoice: Invoice) -> list[ValidationFlag]:
    """Re-run every rule and refresh the flag list.

    Flags a human has explicitly resolved stay resolved, so acknowledging a
    known quirk on a vendor's bill does not have to be repeated after every
    edit.
    """
    resolved = {
        (f.rule, f.field_path) for f in invoice.flags if f.resolved
    }
    invoice.flags.clear()
    db.flush()

    fresh: list[ValidationFlag] = []
    for result in validate_invoice(invoice, confidence=invoice.confidence):
        flag = ValidationFlag(
            rule=result.rule,
            severity=result.severity,
            field_path=result.field_path,
            message=result.message,
            expected=result.expected,
            actual=result.actual,
            resolved=(result.rule, result.field_path) in resolved,
        )
        invoice.flags.append(flag)
        fresh.append(flag)

    open_errors = [f for f in fresh if f.severity == ERROR and not f.resolved]
    open_any = [f for f in fresh if not f.resolved]

    if invoice.status != "confirmed":
        invoice.needs_review = bool(open_any)
        invoice.status = "needs_review" if open_any else "extracted"
    db.flush()
    return fresh


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


def needs_second_reading(prepared: dict, extracted) -> str | None:
    """Which model should read this bill again, if any.

    A second reading is bought where the risk is real, not on every bill:

    * no trustworthy text layer — the model is reading pixels, the hardest case
      and the one where a cheaper model is most likely to slip;
    * a large value — a wrong figure on a big bill costs far more than the
      few paise the extra reading costs;
    * the first reading said it was unsure.

    Everything else is a clean digital PDF of a modest bill, where one reading
    plus the arithmetic checks is proportionate.
    """
    if not settings.enable_crosscheck:
        return None

    second = settings.escalation_model
    if not second or second == settings.extraction_model:
        return None

    if prepared["route"] != ROUTE_TEXT:
        return second

    total = extracted.grand_total
    if total is not None and float(total) >= settings.crosscheck_min_value:
        return second

    if (extracted.overall_confidence or 0) < settings.crosscheck_min_confidence:
        return second

    return None


def process_document(db: Session, document_id: int) -> Invoice | None:
    """Run the whole pipeline for one document."""
    document = db.get(Document, document_id)
    if document is None:
        raise PipelineError(f"Document {document_id} not found")

    document.status = "processing"
    document.error_message = None
    db.flush()

    try:
        prepared = prepare_document(db, document)
        run, extracted = run_extraction(db, document, prepared)

        # Where it matters, read the bill a second time with a stronger model
        # and keep both answers. The disagreements are the point: they are the
        # only evidence available for fields no rule can verify.
        disagreements = []
        second_model = needs_second_reading(prepared, extracted)
        if second_model:
            try:
                run2, extracted2 = run_extraction(
                    db, document, prepared, model=second_model, pass_type="verify"
                )
                disagreements = compare_readings(extracted, extracted2)
                # The stronger model's reading is the one that gets filed.
                run, extracted = run2, extracted2
                log.info(
                    "%s cross-read by %s: %d disagreement(s)",
                    document.original_filename, second_model, len(disagreements),
                )
            except Exception as exc:  # noqa: BLE001 - one good reading still beats none
                log.warning(
                    "second reading of %s failed, keeping the first: %s",
                    document.original_filename, exc,
                )

        # Re-reading a document replaces its previous unconfirmed invoice
        # rather than stacking a second one behind it.
        for old in list(document.invoices):
            if old.status == "confirmed":
                log.info("document %s already confirmed; leaving invoice %s", document.id, old.id)
                document.status = "confirmed"
                db.flush()
                return old
            db.delete(old)
        db.flush()

        invoice = persist_invoice(
            db, document=document, extracted=extracted, extraction_run_id=run.id
        )

        revalidate(db, invoice)

        for d in disagreements:
            invoice.flags.append(
                ValidationFlag(
                    rule="readings_disagree",
                    severity=ERROR,
                    field_path=d.field_path,
                    message=(
                        f"Two independent readings disagree on the {d.label}: "
                        f"{d.first or 'nothing'} against {d.second or 'nothing'}. "
                        "Check this one against the bill."
                    ),
                    expected=d.first,
                    actual=d.second,
                )
            )
        if disagreements:
            invoice.needs_review = True
            invoice.status = "needs_review"
            db.flush()

        # Added after revalidate, which rebuilds the flag list purely from the
        # rule set and has no visibility of other documents.
        duplicate = _find_duplicate_elsewhere(db, invoice)
        if duplicate is not None:
            invoice.flags.append(
                ValidationFlag(
                    rule="duplicate_invoice",
                    severity=ERROR,
                    field_path="invoice_number",
                    message=(
                        f"Invoice {invoice.invoice_number} from this seller for "
                        f"{invoice.financial_year} is already recorded as "
                        f"invoice #{duplicate.id}, uploaded "
                        f"{duplicate.created_at:%d %b %Y}."
                    ),
                )
            )
            invoice.needs_review = True
            invoice.status = "needs_review"
            db.flush()

        _quarantine_first_from_seller(db, invoice)

        compute_brokerage(db, invoice)

        document.status = "needs_review" if invoice.needs_review else "extracted"
        db.flush()
        log.info(
            "document %s -> invoice %s (%s, %s flags)",
            document.id, invoice.id, invoice.status, len(invoice.flags),
        )
        return invoice

    except Exception as exc:
        document.status = "failed"
        document.error_message = str(exc)[:2000]
        db.flush()
        log.exception("pipeline failed for document %s", document_id)
        raise


def _quarantine_first_from_seller(db: Session, invoice: Invoice) -> None:
    """Hold the first bill from a seller we have never seen before.

    Every rule in the validator checks a bill against itself, so a layout the
    reader has misread in some way nothing can verify — a broker on a line we
    do not look at, a column we mapped to the wrong field — passes cleanly.
    The first bill from a new seller is the one moment that is cheap to catch,
    and it costs one review per seller rather than one per bill.
    """
    if invoice.seller_id is None:
        return
    seen = db.scalar(
        select(func.count())
        .select_from(Invoice)
        .where(Invoice.seller_id == invoice.seller_id, Invoice.id != invoice.id)
    )
    if seen:
        return

    seller = db.get(Party, invoice.seller_id)
    name = (seller.display_name or seller.legal_name) if seller else "this seller"
    invoice.flags.append(
        ValidationFlag(
            rule="first_bill_from_seller",
            severity=WARNING,
            field_path="seller",
            message=(
                f"First bill received from {name}. Check the reading against "
                "the document once; later bills in this layout will not be "
                "held."
            ),
        )
    )
    invoice.needs_review = True
    invoice.status = "needs_review"
    db.flush()


def _find_duplicate_elsewhere(db: Session, invoice: Invoice) -> Invoice | None:
    hit = find_duplicate(
        db,
        seller_id=invoice.seller_id,
        invoice_number=invoice.invoice_number,
        fy=invoice.financial_year,
    )
    if hit is not None and hit.id != invoice.id:
        return hit
    return None
