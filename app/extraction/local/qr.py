"""The e-invoice QR code.

Every GST e-invoice carries a QR containing a JWS signed by the Invoice
Registration Portal. Its payload is small but authoritative: the parties, the
document number and date, the invoice value and the IRN. No inference is
involved, and it decodes off the page image, so a broken font map cannot touch
it.

It is a witness, not an oracle. One of the sample bills carries a QR from a
different invoice altogether — a template with a stale image baked in — so
what comes back here is compared against the printed page, never trusted over
it.
"""
from __future__ import annotations

import base64
import binascii
import json
import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# The QR is dense (version 15+ on these bills). Rendering below about 250 DPI
# drops the decode rate sharply; 300 decodes every sample reliably.
DECODE_DPI = 300


@dataclass
class EInvoiceQR:
    """The signed payload of an e-invoice QR."""

    seller_gstin: str | None = None
    buyer_gstin: str | None = None
    doc_no: str | None = None
    doc_type: str | None = None
    doc_date: str | None = None       # as printed in the payload: DD/MM/YYYY
    total_value: float | None = None
    item_count: int | None = None
    main_hsn: str | None = None
    irn: str | None = None
    irn_date: str | None = None
    page_no: int = 0


@dataclass
class EwayQR:
    """The plain-text QR on an e-way bill annexure."""

    eway_bill_no: str | None = None
    gstin: str | None = None
    raw: str = ""
    page_no: int = 0


@dataclass
class QRFindings:
    einvoice: EInvoiceQR | None = None
    eway: EwayQR | None = None
    decoded: int = 0


def _b64url(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def _parse_jws(text: str) -> dict | None:
    """Pull the `data` object out of a JWS without verifying the signature.

    Verification would need the IRP's public certificate, which is a network
    fetch this design does not allow. The signature is not what makes the
    payload useful here — the arithmetic checks are.
    """
    parts = text.split(".")
    if len(parts) < 2:
        return None
    try:
        payload = json.loads(_b64url(parts[1]))
    except (ValueError, binascii.Error, UnicodeDecodeError):
        return None

    data = payload.get("data")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            return None
    return data if isinstance(data, dict) else None


def _as_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _einvoice_from(data: dict, page_no: int) -> EInvoiceQR:
    return EInvoiceQR(
        seller_gstin=(data.get("SellerGstin") or "").strip().upper() or None,
        buyer_gstin=(data.get("BuyerGstin") or "").strip().upper() or None,
        doc_no=(data.get("DocNo") or "").strip() or None,
        doc_type=(data.get("DocTyp") or "").strip() or None,
        doc_date=(data.get("DocDt") or "").strip() or None,
        total_value=_as_float(data.get("TotInvVal")),
        item_count=_as_int(data.get("ItemCnt")),
        main_hsn=(str(data.get("MainHsnCode") or "")).strip() or None,
        irn=(data.get("Irn") or "").strip() or None,
        irn_date=(data.get("IrnDt") or "").strip() or None,
        page_no=page_no,
    )


def _eway_from(text: str, page_no: int) -> EwayQR:
    """Parse 'EWB No. : 112600000000 / GSTIN : 27NGACL2841M1ZO / Date : ...'."""
    fields: dict[str, str] = {}
    for chunk in text.split("/"):
        if ":" not in chunk:
            continue
        label, _, value = chunk.partition(":")
        fields[label.strip().lower().rstrip(".")] = value.strip()
    return EwayQR(
        eway_bill_no=fields.get("ewb no") or None,
        gstin=(fields.get("gstin") or "").upper() or None,
        raw=text,
        page_no=page_no,
    )


def read_qr(pdf_path: Path, max_pages: int = 6) -> QRFindings:
    """Decode every QR in the document, keeping the first of each kind."""
    findings = QRFindings()
    try:
        import pypdfium2 as pdfium
        import zxingcpp
    except ImportError as exc:  # pragma: no cover - optional at import time
        log.warning("QR decoding unavailable (%s); skipping", exc)
        return findings

    try:
        doc = pdfium.PdfDocument(str(pdf_path))
    except Exception as exc:  # noqa: BLE001 - a malformed PDF is not fatal here
        log.warning("could not open %s for QR decoding: %s", pdf_path.name, exc)
        return findings

    try:
        for page_no, page in enumerate(doc, start=1):
            if page_no > max_pages:
                break
            if findings.einvoice and findings.eway:
                break
            try:
                image = page.render(scale=DECODE_DPI / 72).to_pil().convert("L")
                results = zxingcpp.read_barcodes(image)
            except Exception as exc:  # noqa: BLE001
                log.debug("QR scan failed on %s p%s: %s", pdf_path.name, page_no, exc)
                continue

            for result in results:
                text = (result.text or "").strip()
                if not text:
                    continue
                findings.decoded += 1
                if text.startswith("eyJ") and findings.einvoice is None:
                    data = _parse_jws(text)
                    if data:
                        findings.einvoice = _einvoice_from(data, page_no)
                elif "EWB" in text.upper() and findings.eway is None:
                    findings.eway = _eway_from(text, page_no)
    finally:
        doc.close()

    if findings.einvoice:
        log.info("%s: e-invoice QR %s dated %s for %s",
                 pdf_path.name, findings.einvoice.doc_no,
                 findings.einvoice.doc_date, findings.einvoice.total_value)
    return findings
