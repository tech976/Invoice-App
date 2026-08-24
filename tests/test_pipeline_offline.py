"""The pipeline end to end, with only the Claude call replaced by a fixture.

Everything downstream of the model — normalisation, party and product
matching, persistence, arithmetic validation, brokerage accrual, duplicate
detection — is the real code running against a real database.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

from app.extraction import llm, pipeline
from app.extraction.llm import ExtractionResult
from app.ingest.storage import store_file
from app.models import (
    BrokerageEntry,
    Document,
    EwayBill,
    Invoice,
    Party,
    Product,
    ValidationFlag,
)
from tests.fixtures import (
    BROKEN_FONT,
    BROKEN_FONT_INVOICE,
    CRYSTAL_INVOICE,
    CRYSTAL_REPORTS,
    TALLY_GST,
    TALLY_INVOICE,
)

try:
    from tests import local_bills
except ImportError:  # a fresh clone has no local bills
    local_bills = None


def bill_path(slug: str) -> Path:
    """The PDF for a layout slug, or skip.

    The bills are third-party business documents and are not in the
    repository; `tests/local_bills.py` points at them on a machine that has
    them. See `tests/local_bills.example.py`.
    """
    if local_bills is None:
        pytest.skip("tests/local_bills.py not configured — no sample bills")
    path = local_bills.path_for(slug)
    if path is None:
        pytest.skip(f"sample bill for '{slug}' not found")
    return path


def _ingest(db, slug: str, extracted, monkeypatch) -> Invoice:
    """Store a real PDF, then run the real pipeline with a stubbed model."""
    path = bill_path(slug)

    digest, stored, mime = store_file(path)
    doc = Document(
        sha256=digest, original_filename=path.name, stored_path=str(stored),
        mime_type=mime, size_bytes=path.stat().st_size, status="queued",
    )
    db.add(doc)
    db.flush()

    def fake_extract(**kwargs):
        return ExtractionResult(
            invoice=extracted, model="fixture", prompt_version="test",
            input_tokens=0, output_tokens=0, duration_ms=0,
            raw=extracted.model_dump(mode="json"),
        )

    monkeypatch.setattr(llm, "extract_invoice", fake_extract)
    invoice = pipeline.process_document(db, doc.id)
    db.commit()
    return invoice


# --------------------------------------------------------------------------


def test_adon_to_lcdf_is_read_and_balances(db, monkeypatch):
    inv = _ingest(db, TALLY_GST, TALLY_INVOICE, monkeypatch)

    assert inv.invoice_number == "NGA/001634/26-27"
    assert inv.invoice_date == date(2026, 7, 21)
    assert inv.financial_year == "2026-27"
    assert inv.grand_total == Decimal("6139238.00")
    assert inv.supply_type == "intra"
    # '7 Days' payment terms on a 21 July bill.
    assert inv.due_date == date(2026, 7, 28)

    assert inv.seller.gstin == "27NGACL2841M1ZO"
    assert inv.seller.state_name == "Maharashtra"
    assert inv.buyer.gstin == "27RVSTL7392R1ZI"
    # Consignee identical to the buyer is dropped rather than duplicated.
    assert inv.consignee_id is None
    assert inv.broker.legal_name == "Ramesh Kulkarni"

    assert len(inv.lines) == 3
    assert inv.lines[0].bags == 750 or inv.lines[0].bags == Decimal("200.000")
    assert inv.lines[0].brand == "Andesfood"
    assert inv.lines[0].hsn == "08023100"
    assert inv.total_bags == Decimal("350.000")

    ewb = db.scalar(select(EwayBill).where(EwayBill.invoice_id == inv.id))
    assert ewb.eway_bill_no == "100000000001"
    assert ewb.vehicle_no == "MH04AA1001"
    assert inv.bank_ifsc == "EXMP0000001"

    errors = [f for f in inv.flags if f.severity == "error" and not f.resolved]
    assert not errors, [f.message for f in errors]
    assert inv.status == "extracted"
    assert inv.needs_review is False


def test_garbled_pdf_bill_still_balances(db, monkeypatch):
    """The bill whose embedded text is mojibake — routed through OCR/vision."""
    inv = _ingest(db, BROKEN_FONT, BROKEN_FONT_INVOICE, monkeypatch)

    assert inv.document.extraction_route == "ocr_vision"
    assert inv.document.text_quality < 0.1
    assert inv.supply_type == "inter"
    assert inv.igst_amount == Decimal("42042.26")
    assert inv.cgst_amount is None
    assert inv.grand_total == Decimal("884463.00")

    kinds = {c.kind: c.amount for c in inv.charges}
    assert kinds["discount"] == Decimal("-12804.75")
    assert kinds["handling"] == Decimal("1575.00")

    ewb = db.scalar(select(EwayBill).where(EwayBill.invoice_id == inv.id))
    assert ewb.transporter_name == "Vega Cargo Movers"
    assert inv.transporter is not None
    assert inv.transporter.gstin == "29VEGPM3384H1ZL"
    assert inv.transporter.is_transporter is True

    errors = [f for f in inv.flags if f.severity == "error" and not f.resolved]
    assert not errors, [f.message for f in errors]


def test_ashapura_packing_charge_is_taxed_with_goods(db, monkeypatch):
    inv = _ingest(db, CRYSTAL_REPORTS, CRYSTAL_INVOICE, monkeypatch)

    assert inv.taxable_value == Decimal("871633.95")
    assert inv.grand_total == Decimal("915216.00")
    # '0802.1200' normalises to digits only.
    assert inv.lines[0].hsn == "08021200"
    assert inv.lines[0].discount_pct == Decimal("1.5000")
    assert inv.broker.legal_name == "Suresh Deshmukh ( Suresh C 12 )"
    assert inv.broker.is_broker is True
    # Round-off is not printed on this bill; 0.35 is inside tolerance.
    errors = [f for f in inv.flags if f.severity == "error" and not f.resolved]
    assert not errors, [f.message for f in errors]

    assert len(inv.unmapped_fields) == 2


def test_same_buyer_across_two_vendors_resolves_to_one_party(db, monkeypatch):
    """'Sunrise Traders' and 'SUNRISE TRADERS -Karnataka' are one firm."""
    _ingest(db, BROKEN_FONT, BROKEN_FONT_INVOICE, monkeypatch)
    _ingest(db, CRYSTAL_REPORTS, CRYSTAL_INVOICE, monkeypatch)

    sunrise = db.scalars(
        select(Party).where(Party.gstin == "29SNRTB4426N2ZQ")
    ).all()
    assert len(sunrise) == 1, "the same GSTIN must not create two parties"
    aliases = {a.alias for a in sunrise[0].aliases}
    assert "SUNRISE TRADERS -Karnataka" in aliases


def test_products_are_canonicalised_per_grade(db, monkeypatch):
    _ingest(db, TALLY_GST, TALLY_INVOICE, monkeypatch)
    products = db.scalars(select(Product)).all()
    names = {p.canonical_name for p in products}
    # Three walnut grades stay three products — they trade at different prices.
    assert len(names) == 3
    assert all(p.category == "Walnuts" for p in products)
    assert all(p.default_hsn == "08023100" for p in products)


def test_brokerage_accrues_at_the_default_rate(db, monkeypatch):
    inv = _ingest(db, TALLY_GST, TALLY_INVOICE, monkeypatch)
    entry = db.scalar(select(BrokerageEntry).where(BrokerageEntry.invoice_id == inv.id))
    assert entry is not None
    # 1% of the taxable value.
    assert entry.amount == Decimal("58468.93")
    assert entry.financial_year == "2026-27"
    assert entry.status == "accrued"


def test_duplicate_invoice_number_is_flagged_not_lost(db, monkeypatch):
    """A rescan of the same bill under a different filename must be caught."""
    _ingest(db, TALLY_GST, TALLY_INVOICE, monkeypatch)

    # A second document, same invoice number and seller.
    second = Document(
        sha256="f" * 64, original_filename="rescan.pdf",
        stored_path=str(bill_path(TALLY_GST)),
        mime_type="application/pdf", size_bytes=1, status="queued",
    )
    db.add(second)
    db.flush()
    monkeypatch.setattr(
        llm, "extract_invoice",
        lambda **kw: ExtractionResult(
            invoice=TALLY_INVOICE, model="fixture", prompt_version="test",
            input_tokens=0, output_tokens=0, duration_ms=0, raw={},
        ),
    )
    dup = pipeline.process_document(db, second.id)
    db.commit()

    rules = {f.rule for f in dup.flags}
    assert "duplicate_invoice" in rules
    assert dup.needs_review is True
    assert dup.status == "needs_review"


def test_bad_extraction_lands_in_the_review_queue(db, monkeypatch):
    """A misread total must fail the checks rather than be filed silently."""
    broken = TALLY_INVOICE.model_copy(deep=True)
    broken.grand_total = 613923.80        # decimal point slipped
    broken.overall_confidence = 0.55

    inv = _ingest(db, TALLY_GST, broken, monkeypatch)

    rules = {f.rule for f in inv.flags}
    assert "amount_in_words_mismatch" in rules
    assert "grand_total_mismatch" in rules
    assert "low_confidence" in rules
    assert inv.needs_review is True
    assert inv.status == "needs_review"


def test_totals_are_derived_when_the_bill_omits_them(db, monkeypatch):
    """Many smaller bills print only a grand total. The rest must be computed."""
    sparse = TALLY_INVOICE.model_copy(deep=True)
    sparse.subtotal = None
    sparse.taxable_value = None
    sparse.total_quantity = None
    sparse.total_bags = None
    sparse.other_charges = None
    sparse.round_off = None

    inv = _ingest(db, TALLY_GST, sparse, monkeypatch)

    assert inv.subtotal == Decimal("5846893.00")      # sum of the three rows
    assert inv.taxable_value == Decimal("5846893.00")  # no discount rows
    assert inv.total_quantity == Decimal("8750.000")
    assert inv.total_quantity_uom == "KGS"
    assert inv.total_bags == Decimal("350.000")
    assert inv.round_off == Decimal("0.34")            # taken from the charge row

    errors = [f for f in inv.flags if f.severity == "error" and not f.resolved]
    assert not errors, [f.message for f in errors]


def test_a_printed_total_is_never_overwritten_by_a_computed_one(db, monkeypatch):
    """If the bill says 100 and the rows say 90, the bill wins and it is flagged.

    Silently replacing the printed figure would hide the disagreement, which
    is precisely the thing a human needs to see.
    """
    odd = TALLY_INVOICE.model_copy(deep=True)
    odd.taxable_value = 5000000.00

    inv = _ingest(db, TALLY_GST, odd, monkeypatch)

    assert inv.taxable_value == Decimal("5000000.00")
    assert "taxable_value_mismatch" in {f.rule for f in inv.flags}


# ---------------------------------------------------------------------------
# Tiered cross-reading
# ---------------------------------------------------------------------------


def test_second_reading_is_bought_only_where_risk_is_real(monkeypatch):
    from app.config import settings
    from app.extraction.pipeline import ROUTE_OCR, ROUTE_TEXT, needs_second_reading

    monkeypatch.setattr(settings, "enable_crosscheck", True)
    monkeypatch.setattr(settings, "extraction_model", "claude-sonnet-5")
    monkeypatch.setattr(settings, "escalation_model", "claude-opus-5")
    monkeypatch.setattr(settings, "crosscheck_min_value", 1_000_000.0)

    small = TALLY_INVOICE.model_copy(deep=True)
    small.grand_total = 9000
    small.overall_confidence = 0.97

    # A clean, small, confidently-read bill is not worth a second reading.
    assert needs_second_reading({"route": ROUTE_TEXT}, small) is None
    # A large one is.
    assert needs_second_reading({"route": ROUTE_TEXT}, TALLY_INVOICE) == "claude-opus-5"
    # So is anything read off pixels rather than a text layer.
    assert needs_second_reading({"route": ROUTE_OCR}, small) == "claude-opus-5"
    # So is anything the first reading was unsure about.
    unsure = small.model_copy(deep=True)
    unsure.overall_confidence = 0.70
    assert needs_second_reading({"route": ROUTE_TEXT}, unsure) == "claude-opus-5"

    monkeypatch.setattr(settings, "enable_crosscheck", False)
    assert needs_second_reading({"route": ROUTE_OCR}, TALLY_INVOICE) is None


def test_disagreeing_readings_reach_the_review_queue(db, monkeypatch):
    """The point of the second reading: catch what no rule can.

    A wrong broker name is arithmetically perfect — every total still balances
    — so only a second opinion surfaces it.
    """
    from app.config import settings
    from app.extraction.llm import ExtractionResult

    monkeypatch.setattr(settings, "enable_crosscheck", True)
    monkeypatch.setattr(settings, "extraction_model", "claude-sonnet-5")
    monkeypatch.setattr(settings, "escalation_model", "claude-opus-5")

    first = TALLY_INVOICE.model_copy(deep=True)
    first.broker_name = "Ramesh Kulkarnl"      # a plausible misreading

    path = bill_path(TALLY_GST)
    digest, stored, mime = store_file(path)
    doc = Document(
        sha256=digest, original_filename=path.name, stored_path=str(stored),
        mime_type=mime, size_bytes=path.stat().st_size, status="queued",
    )
    db.add(doc)
    db.flush()

    calls = []

    def fake_extract(**kwargs):
        model = kwargs.get("model")
        calls.append(model)
        payload = first if len(calls) == 1 else TALLY_INVOICE
        return ExtractionResult(
            invoice=payload, model=model or "?", prompt_version="test",
            input_tokens=0, output_tokens=0, duration_ms=0, raw={},
        )

    monkeypatch.setattr(llm, "extract_invoice", fake_extract)
    invoice = pipeline.process_document(db, doc.id)
    db.commit()

    # The bill is over the value threshold, so it was read twice.
    assert calls == ["claude-sonnet-5", "claude-opus-5"]

    flags = [f for f in invoice.flags if f.rule == "readings_disagree"]
    assert flags, "the broker-name disagreement should have been flagged"
    assert "broker" in flags[0].message
    assert invoice.status == "needs_review"
    # The stronger model's reading is the one filed.
    assert invoice.broker.legal_name == "Ramesh Kulkarni"
