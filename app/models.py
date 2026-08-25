"""ORM models.

Design notes
------------
* Money is ``Numeric(18, 2)``, quantity ``Numeric(18, 3)``, rates ``Numeric(18, 4)``
  so nothing is lost to float drift when rolling up crores of turnover.
* ``parties`` is one table for sellers, buyers, consignees, transporters and
  brokers. The same firm is often a buyer on one bill and a seller on the next,
  so roles are flags rather than separate tables.
* Every extracted value keeps a path back to the page it came from:
  invoice -> extraction_run -> document -> document_pages -> stored PDF.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

MONEY = Numeric(18, 2)
QTY = Numeric(18, 3)
RATE = Numeric(18, 4)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


# ===========================================================================
# Parties
# ===========================================================================


class Party(TimestampMixin, Base):
    """Any company appearing on a bill."""

    __tablename__ = "parties"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Canonical identity. GSTIN is the strongest key we get on Indian bills.
    gstin: Mapped[Optional[str]] = mapped_column(String(15), unique=True, index=True)
    pan: Mapped[Optional[str]] = mapped_column(String(10), index=True)
    fssai: Mapped[Optional[str]] = mapped_column(String(20))

    legal_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Lowercased, punctuation-stripped name used for fuzzy matching when a
    # bill carries no GSTIN.
    normalized_name: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    display_name: Mapped[Optional[str]] = mapped_column(String(255))

    address: Mapped[Optional[str]] = mapped_column(Text)
    city: Mapped[Optional[str]] = mapped_column(String(120))
    state_name: Mapped[Optional[str]] = mapped_column(String(80))
    state_code: Mapped[Optional[str]] = mapped_column(String(2), index=True)
    pincode: Mapped[Optional[str]] = mapped_column(String(10))
    phone: Mapped[Optional[str]] = mapped_column(String(120))
    email: Mapped[Optional[str]] = mapped_column(String(180))

    # Roles this party has actually been seen in.
    is_seller: Mapped[bool] = mapped_column(Boolean, default=False)
    is_buyer: Mapped[bool] = mapped_column(Boolean, default=False)
    is_transporter: Mapped[bool] = mapped_column(Boolean, default=False)
    is_broker: Mapped[bool] = mapped_column(Boolean, default=False)

    # Operational fields the broker maintains by hand.
    credit_days: Mapped[Optional[int]] = mapped_column(Integer)
    credit_limit: Mapped[Optional[float]] = mapped_column(MONEY)
    contact_person: Mapped[Optional[str]] = mapped_column(String(180))
    notes: Mapped[Optional[str]] = mapped_column(Text)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)

    aliases: Mapped[list["PartyAlias"]] = relationship(
        back_populates="party", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("ix_parties_name_state", "normalized_name", "state_code"),)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Party {self.id} {self.legal_name!r} {self.gstin}>"


class PartyAlias(TimestampMixin, Base):
    """Name variants seen in the wild.

    'SUNRISE TRADERS -Karnataka', 'Sunrise Traders' and 'SUNRISE TRDRS' are
    the same firm; each spelling gets recorded here so the next bill matches
    without a human.
    """

    __tablename__ = "party_aliases"

    id: Mapped[int] = mapped_column(primary_key=True)
    party_id: Mapped[int] = mapped_column(ForeignKey("parties.id", ondelete="CASCADE"), index=True)
    alias: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_alias: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    source: Mapped[str] = mapped_column(String(40), default="extraction")
    seen_count: Mapped[int] = mapped_column(Integer, default=1)

    party: Mapped[Party] = relationship(back_populates="aliases")

    __table_args__ = (UniqueConstraint("party_id", "normalized_alias", name="uq_party_alias"),)


# ===========================================================================
# Documents (the uploaded bills, kept forever)
# ===========================================================================


class Document(TimestampMixin, Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Content hash doubles as the dedupe key and the storage filename.
    sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    original_filename: Mapped[str] = mapped_column(String(400), nullable=False)
    stored_path: Mapped[str] = mapped_column(String(600), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(120), default="application/pdf")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    page_count: Mapped[Optional[int]] = mapped_column(Integer)

    # uploaded -> queued -> processing -> extracted / needs_review
    #          -> confirmed | failed | duplicate
    status: Mapped[str] = mapped_column(String(24), default="uploaded", index=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text)

    # How readable was the embedded text layer? Drives OCR/vision routing.
    text_quality: Mapped[Optional[float]] = mapped_column(Float)
    extraction_route: Mapped[Optional[str]] = mapped_column(String(30))
    producer: Mapped[Optional[str]] = mapped_column(String(200))

    uploaded_by: Mapped[Optional[str]] = mapped_column(String(180))
    source: Mapped[str] = mapped_column(String(40), default="web_upload")
    notes: Mapped[Optional[str]] = mapped_column(Text)

    pages: Mapped[list["DocumentPage"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="DocumentPage.page_no"
    )
    runs: Mapped[list["ExtractionRun"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    invoices: Mapped[list["Invoice"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class DocumentPage(Base):
    __tablename__ = "document_pages"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    page_no: Mapped[int] = mapped_column(Integer, nullable=False)
    image_path: Mapped[Optional[str]] = mapped_column(String(600))
    text_layer: Mapped[Optional[str]] = mapped_column(Text)
    ocr_text: Mapped[Optional[str]] = mapped_column(Text)
    text_quality: Mapped[Optional[float]] = mapped_column(Float)
    width: Mapped[Optional[int]] = mapped_column(Integer)
    height: Mapped[Optional[int]] = mapped_column(Integer)

    document: Mapped[Document] = relationship(back_populates="pages")

    __table_args__ = (UniqueConstraint("document_id", "page_no", name="uq_doc_page"),)


class ExtractionRun(Base):
    """One attempt at reading a document. Kept for audit and for re-runs."""

    __tablename__ = "extraction_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )

    engine: Mapped[str] = mapped_column(String(40), default="claude")
    model: Mapped[Optional[str]] = mapped_column(String(80))
    prompt_version: Mapped[Optional[str]] = mapped_column(String(20))
    pass_type: Mapped[str] = mapped_column(String(20), default="primary")  # primary|verify|manual

    status: Mapped[str] = mapped_column(String(20), default="running")
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer)

    input_tokens: Mapped[Optional[int]] = mapped_column(Integer)
    output_tokens: Mapped[Optional[int]] = mapped_column(Integer)
    cost_usd: Mapped[Optional[float]] = mapped_column(Numeric(12, 6))

    raw_output: Mapped[Optional[dict]] = mapped_column(JSON)
    error_message: Mapped[Optional[str]] = mapped_column(Text)

    document: Mapped[Document] = relationship(back_populates="runs")


# ===========================================================================
# Invoices
# ===========================================================================


class Invoice(TimestampMixin, Base):
    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    extraction_run_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("extraction_runs.id", ondelete="SET NULL")
    )

    # -- identity ------------------------------------------------------
    document_type: Mapped[str] = mapped_column(String(30), default="tax_invoice", index=True)
    invoice_number: Mapped[Optional[str]] = mapped_column(String(80), index=True)
    invoice_date: Mapped[Optional[date]] = mapped_column(Date, index=True)
    due_date: Mapped[Optional[date]] = mapped_column(Date, index=True)
    financial_year: Mapped[Optional[str]] = mapped_column(String(9), index=True)  # '2026-27'
    irn: Mapped[Optional[str]] = mapped_column(String(80), index=True)
    ack_no: Mapped[Optional[str]] = mapped_column(String(40))
    ack_date: Mapped[Optional[date]] = mapped_column(Date)
    po_number: Mapped[Optional[str]] = mapped_column(String(80))
    po_date: Mapped[Optional[date]] = mapped_column(Date)
    delivery_note: Mapped[Optional[str]] = mapped_column(String(120))
    delivery_note_date: Mapped[Optional[date]] = mapped_column(Date)

    # -- parties -------------------------------------------------------
    seller_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("parties.id", ondelete="SET NULL"), index=True
    )
    buyer_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("parties.id", ondelete="SET NULL"), index=True
    )
    consignee_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("parties.id", ondelete="SET NULL"), index=True
    )
    transporter_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("parties.id", ondelete="SET NULL"), index=True
    )
    broker_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("parties.id", ondelete="SET NULL"), index=True
    )
    broker_name_raw: Mapped[Optional[str]] = mapped_column(String(180))

    # -- terms ---------------------------------------------------------
    place_of_supply: Mapped[Optional[str]] = mapped_column(String(80))
    supply_type: Mapped[Optional[str]] = mapped_column(String(12))  # intra | inter
    payment_terms: Mapped[Optional[str]] = mapped_column(String(120))
    currency: Mapped[str] = mapped_column(String(3), default="INR")

    # -- money ---------------------------------------------------------
    total_quantity: Mapped[Optional[float]] = mapped_column(QTY)
    total_quantity_uom: Mapped[Optional[str]] = mapped_column(String(12))
    total_bags: Mapped[Optional[float]] = mapped_column(QTY)
    subtotal: Mapped[Optional[float]] = mapped_column(MONEY)
    discount_total: Mapped[Optional[float]] = mapped_column(MONEY)
    taxable_value: Mapped[Optional[float]] = mapped_column(MONEY)
    cgst_amount: Mapped[Optional[float]] = mapped_column(MONEY)
    sgst_amount: Mapped[Optional[float]] = mapped_column(MONEY)
    igst_amount: Mapped[Optional[float]] = mapped_column(MONEY)
    cess_amount: Mapped[Optional[float]] = mapped_column(MONEY)
    tcs_amount: Mapped[Optional[float]] = mapped_column(MONEY)
    other_charges: Mapped[Optional[float]] = mapped_column(MONEY)
    round_off: Mapped[Optional[float]] = mapped_column(MONEY)
    grand_total: Mapped[Optional[float]] = mapped_column(MONEY, index=True)
    amount_in_words: Mapped[Optional[str]] = mapped_column(Text)

    # -- logistics / misc ---------------------------------------------
    eway_bill_no: Mapped[Optional[str]] = mapped_column(String(20), index=True)
    vehicle_no: Mapped[Optional[str]] = mapped_column(String(20), index=True)
    bank_name: Mapped[Optional[str]] = mapped_column(String(120))
    bank_account_no: Mapped[Optional[str]] = mapped_column(String(40))
    bank_ifsc: Mapped[Optional[str]] = mapped_column(String(15))
    bank_branch: Mapped[Optional[str]] = mapped_column(String(120))
    remarks: Mapped[Optional[str]] = mapped_column(Text)
    terms: Mapped[Optional[str]] = mapped_column(Text)

    # -- state ---------------------------------------------------------
    # extracted -> needs_review -> confirmed  (or: duplicate / rejected)
    status: Mapped[str] = mapped_column(String(20), default="extracted", index=True)
    confidence: Mapped[Optional[float]] = mapped_column(Float, index=True)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(180))
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    vendor_format_hint: Mapped[Optional[str]] = mapped_column(String(80), index=True)

    # Anything printed on the bill that the schema had no home for.
    unmapped_fields: Mapped[Optional[list]] = mapped_column(JSON)

    # -- payment rollup (maintained by payment allocations) -------------
    amount_paid: Mapped[float] = mapped_column(MONEY, default=0)
    payment_status: Mapped[str] = mapped_column(String(20), default="unpaid", index=True)

    document: Mapped[Document] = relationship(back_populates="invoices")
    seller: Mapped[Optional[Party]] = relationship(foreign_keys=[seller_id])
    buyer: Mapped[Optional[Party]] = relationship(foreign_keys=[buyer_id])
    consignee: Mapped[Optional[Party]] = relationship(foreign_keys=[consignee_id])
    transporter: Mapped[Optional[Party]] = relationship(foreign_keys=[transporter_id])
    broker: Mapped[Optional[Party]] = relationship(foreign_keys=[broker_id])

    lines: Mapped[list["InvoiceLine"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan", order_by="InvoiceLine.line_no"
    )
    charges: Mapped[list["InvoiceCharge"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan"
    )
    tax_rows: Mapped[list["InvoiceTaxRow"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan"
    )
    eway_bill: Mapped[Optional["EwayBill"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan", uselist=False
    )
    flags: Mapped[list["ValidationFlag"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan"
    )

    __table_args__ = (
        # One invoice number per seller per financial year — but enforced only
        # once an invoice is confirmed. A suspected duplicate still needs to be
        # stored so a human can compare the two side by side and decide which
        # is real; blocking the insert would just lose the evidence.
        Index(
            "uq_invoice_confirmed_number",
            "seller_id", "invoice_number", "financial_year",
            unique=True,
            postgresql_where=text("status = 'confirmed'"),
            sqlite_where=text("status = 'confirmed'"),
        ),
        Index("ix_invoices_seller_date", "seller_id", "invoice_date"),
        Index("ix_invoices_buyer_date", "buyer_id", "invoice_date"),
    )


class InvoiceLine(Base):
    __tablename__ = "invoice_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"), index=True
    )
    line_no: Mapped[int] = mapped_column(Integer, default=1)

    description: Mapped[Optional[str]] = mapped_column(Text)
    item_code: Mapped[Optional[str]] = mapped_column(String(80), index=True)
    item_remarks: Mapped[Optional[str]] = mapped_column(String(200))
    brand: Mapped[Optional[str]] = mapped_column(String(120))
    hsn: Mapped[Optional[str]] = mapped_column(String(12), index=True)

    bags: Mapped[Optional[float]] = mapped_column(QTY)
    quantity: Mapped[Optional[float]] = mapped_column(QTY)
    uom: Mapped[Optional[str]] = mapped_column(String(12))
    rate: Mapped[Optional[float]] = mapped_column(RATE)
    rate_uom: Mapped[Optional[str]] = mapped_column(String(12))
    discount_pct: Mapped[Optional[float]] = mapped_column(Numeric(9, 4))
    discount_amount: Mapped[Optional[float]] = mapped_column(MONEY)
    taxable_amount: Mapped[Optional[float]] = mapped_column(MONEY)
    tax_rate: Mapped[Optional[float]] = mapped_column(Numeric(9, 4))
    cgst_amount: Mapped[Optional[float]] = mapped_column(MONEY)
    sgst_amount: Mapped[Optional[float]] = mapped_column(MONEY)
    igst_amount: Mapped[Optional[float]] = mapped_column(MONEY)
    cess_amount: Mapped[Optional[float]] = mapped_column(MONEY)
    line_total: Mapped[Optional[float]] = mapped_column(MONEY)

    product_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("products.id", ondelete="SET NULL"), index=True
    )

    invoice: Mapped[Invoice] = relationship(back_populates="lines")
    product: Mapped[Optional["Product"]] = relationship()


class InvoiceCharge(Base):
    """Freight, packing, handling, discount, round-off, TCS."""

    __tablename__ = "invoice_charges"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"), index=True
    )
    label: Mapped[str] = mapped_column(String(160), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), default="other", index=True)
    amount: Mapped[float] = mapped_column(MONEY, default=0)
    hsn: Mapped[Optional[str]] = mapped_column(String(12))
    tax_rate: Mapped[Optional[float]] = mapped_column(Numeric(9, 4))

    invoice: Mapped[Invoice] = relationship(back_populates="charges")


class InvoiceTaxRow(Base):
    """A row of the HSN-wise tax summary printed at the foot of the bill."""

    __tablename__ = "invoice_tax_rows"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"), index=True
    )
    hsn: Mapped[Optional[str]] = mapped_column(String(12), index=True)
    taxable_value: Mapped[Optional[float]] = mapped_column(MONEY)
    cgst_rate: Mapped[Optional[float]] = mapped_column(Numeric(9, 4))
    cgst_amount: Mapped[Optional[float]] = mapped_column(MONEY)
    sgst_rate: Mapped[Optional[float]] = mapped_column(Numeric(9, 4))
    sgst_amount: Mapped[Optional[float]] = mapped_column(MONEY)
    igst_rate: Mapped[Optional[float]] = mapped_column(Numeric(9, 4))
    igst_amount: Mapped[Optional[float]] = mapped_column(MONEY)
    cess_amount: Mapped[Optional[float]] = mapped_column(MONEY)
    total_tax: Mapped[Optional[float]] = mapped_column(MONEY)

    invoice: Mapped[Invoice] = relationship(back_populates="tax_rows")


class EwayBill(Base):
    __tablename__ = "eway_bills"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"), unique=True, index=True
    )

    eway_bill_no: Mapped[Optional[str]] = mapped_column(String(20), index=True)
    generated_date: Mapped[Optional[date]] = mapped_column(Date)
    generated_by: Mapped[Optional[str]] = mapped_column(String(15))
    valid_upto: Mapped[Optional[date]] = mapped_column(Date)
    mode: Mapped[Optional[str]] = mapped_column(String(20))
    approx_distance_km: Mapped[Optional[float]] = mapped_column(Float)
    supply_type: Mapped[Optional[str]] = mapped_column(String(40))
    transaction_type: Mapped[Optional[str]] = mapped_column(String(60))
    dispatch_from: Mapped[Optional[str]] = mapped_column(Text)
    ship_to: Mapped[Optional[str]] = mapped_column(Text)

    transporter_id_no: Mapped[Optional[str]] = mapped_column(String(20), index=True)
    transporter_name: Mapped[Optional[str]] = mapped_column(String(200))
    transporter_doc_no: Mapped[Optional[str]] = mapped_column(String(60))
    transporter_doc_date: Mapped[Optional[date]] = mapped_column(Date)
    vehicle_no: Mapped[Optional[str]] = mapped_column(String(20), index=True)
    vehicle_from: Mapped[Optional[str]] = mapped_column(String(120))
    cewb_no: Mapped[Optional[str]] = mapped_column(String(20))

    invoice: Mapped[Invoice] = relationship(back_populates="eway_bill")


# ===========================================================================
# Product canonicalisation
# ===========================================================================


class Product(TimestampMixin, Base):
    """Canonical commodity, so 'Walnuts Inshell 30-34' and 'WALNUT INSHELL
    30/34' roll up to one line in the reports."""

    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    canonical_name: Mapped[str] = mapped_column(String(200), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(200), index=True, nullable=False)
    category: Mapped[Optional[str]] = mapped_column(String(80), index=True)
    grade: Mapped[Optional[str]] = mapped_column(String(60))
    default_hsn: Mapped[Optional[str]] = mapped_column(String(12), index=True)
    default_uom: Mapped[Optional[str]] = mapped_column(String(12))
    default_tax_rate: Mapped[Optional[float]] = mapped_column(Numeric(9, 4))

    __table_args__ = (UniqueConstraint("normalized_name", "grade", name="uq_product_name_grade"),)


class ProductAlias(Base):
    __tablename__ = "product_aliases"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    alias: Mapped[str] = mapped_column(String(220), nullable=False)
    normalized_alias: Mapped[str] = mapped_column(String(220), index=True, nullable=False)
    seller_id: Mapped[Optional[int]] = mapped_column(ForeignKey("parties.id", ondelete="SET NULL"))
    seen_count: Mapped[int] = mapped_column(Integer, default=1)

    __table_args__ = (
        UniqueConstraint("product_id", "normalized_alias", name="uq_product_alias"),
    )


# ===========================================================================
# Quality control
# ===========================================================================


class ValidationFlag(Base):
    """A deterministic check that failed, or a field the model was unsure of.

    These drive the review queue — an invoice with an unresolved error flag
    never reaches 'confirmed'.
    """

    __tablename__ = "validation_flags"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"), index=True
    )
    rule: Mapped[str] = mapped_column(String(60), index=True)
    severity: Mapped[str] = mapped_column(String(12), default="warning", index=True)  # error|warning|info
    field_path: Mapped[Optional[str]] = mapped_column(String(120))
    message: Mapped[str] = mapped_column(Text, nullable=False)
    expected: Mapped[Optional[str]] = mapped_column(String(120))
    actual: Mapped[Optional[str]] = mapped_column(String(120))
    resolved: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    resolved_by: Mapped[Optional[str]] = mapped_column(String(180))
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    invoice: Mapped[Invoice] = relationship(back_populates="flags")


class Correction(Base):
    """Every human edit to an extracted value.

    This is the training signal: when the same field on the same vendor format
    keeps getting corrected the same way, the prompt or a format rule needs
    fixing.
    """

    __tablename__ = "corrections"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"), index=True
    )
    entity: Mapped[str] = mapped_column(String(40), default="invoice")  # invoice|line|eway
    entity_id: Mapped[Optional[int]] = mapped_column(Integer)
    field_path: Mapped[str] = mapped_column(String(120), index=True)
    old_value: Mapped[Optional[str]] = mapped_column(Text)
    new_value: Mapped[Optional[str]] = mapped_column(Text)
    vendor_format_hint: Mapped[Optional[str]] = mapped_column(String(80), index=True)
    corrected_by: Mapped[Optional[str]] = mapped_column(String(180))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# ===========================================================================
# Brokerage, payments, shipments  (the layer this feeds into)
# ===========================================================================


# --------------------------------------------------------------------------
# Voice trade book
#
# Entirely self-contained: one table, no foreign keys, nothing shared with the
# invoice ledger. The two are different things — this is what the broker said
# into his phone, that is what a seller posted him afterwards — and neither
# needs the other to exist. Parties and goods are stored as spoken, in plain
# text, because he knows who C31 is and there is no list to keep.
# --------------------------------------------------------------------------


class VoiceClip(TimestampMixin, Base):
    """A recording, what the machine heard, and what was actually said.

    The third of those is the valuable one. A recogniser can only be taught by
    example, and every clip the broker corrects is an example — his voice, his
    accent, his market's noise, his names. Kept here they accumulate into the
    dataset that a fine-tune needs, as a by-product of using the thing rather
    than as a separate chore.
    """

    __tablename__ = "voice_clips"

    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    heard: Mapped[Optional[str]] = mapped_column(Text)
    # What the broker says was really said. Null until somebody confirms it.
    said: Mapped[Optional[str]] = mapped_column(Text)
    engine: Mapped[Optional[str]] = mapped_column(String(60))
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer)
    language: Mapped[Optional[str]] = mapped_column(String(12))
    # 'new' until reviewed, then 'confirmed' (heard was right) or 'corrected'.
    status: Mapped[str] = mapped_column(String(20), default="new", index=True)


class Trade(TimestampMixin, Base):
    """A deal as the broker dictated it.

    `heard` keeps the raw transcription and `parsed` what was made of it, so a
    disputed entry can be traced back to the words actually spoken. Nothing
    reaches this table until a person has read the fields back — a dictated
    rate has no second source to check it against.
    """

    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(primary_key=True)
    traded_on: Mapped[Optional[date]] = mapped_column(Date, index=True)

    # As said: a code like 'C31', or a name, or whatever he called them.
    seller: Mapped[Optional[str]] = mapped_column(String(120), index=True)
    buyer: Mapped[Optional[str]] = mapped_column(String(120), index=True)
    goods: Mapped[Optional[str]] = mapped_column(String(160), index=True)

    quantity: Mapped[Optional[float]] = mapped_column(Numeric(16, 3))
    uom: Mapped[Optional[str]] = mapped_column(String(12))
    rate: Mapped[Optional[float]] = mapped_column(Numeric(16, 4))
    value: Mapped[Optional[float]] = mapped_column(Numeric(16, 2))

    heard: Mapped[Optional[str]] = mapped_column(Text)
    parsed: Mapped[Optional[dict]] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String(20), default="voice")
    status: Mapped[str] = mapped_column(String(20), default="booked", index=True)
    notes: Mapped[Optional[str]] = mapped_column(Text)



class BrokerageRule(TimestampMixin, Base):
    """How much the broker earns on a bill.

    Most specific match wins: seller+buyer+product > seller+buyer > seller >
    global default.
    """

    __tablename__ = "brokerage_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[Optional[str]] = mapped_column(String(160))
    seller_id: Mapped[Optional[int]] = mapped_column(ForeignKey("parties.id", ondelete="CASCADE"))
    buyer_id: Mapped[Optional[int]] = mapped_column(ForeignKey("parties.id", ondelete="CASCADE"))
    product_id: Mapped[Optional[int]] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"))
    hsn: Mapped[Optional[str]] = mapped_column(String(12))

    # Percentage of the basis, or a flat per-unit rate.
    rate_pct: Mapped[Optional[float]] = mapped_column(Numeric(9, 4))
    rate_per_unit: Mapped[Optional[float]] = mapped_column(RATE)
    basis: Mapped[str] = mapped_column(String(20), default="taxable_value")

    # Which side pays the brokerage.
    payable_by: Mapped[str] = mapped_column(String(12), default="seller")  # seller|buyer|both
    effective_from: Mapped[Optional[date]] = mapped_column(Date)
    effective_to: Mapped[Optional[date]] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[Optional[str]] = mapped_column(Text)


class BrokerageEntry(TimestampMixin, Base):
    """Brokerage accrued on one invoice, settled at year end."""

    __tablename__ = "brokerage_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"), unique=True, index=True
    )
    rule_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("brokerage_rules.id", ondelete="SET NULL")
    )
    broker_id: Mapped[Optional[int]] = mapped_column(ForeignKey("parties.id", ondelete="SET NULL"))

    basis_amount: Mapped[Optional[float]] = mapped_column(MONEY)
    rate_pct: Mapped[Optional[float]] = mapped_column(Numeric(9, 4))
    amount: Mapped[Optional[float]] = mapped_column(MONEY)
    payable_by: Mapped[Optional[str]] = mapped_column(String(12))
    financial_year: Mapped[Optional[str]] = mapped_column(String(9), index=True)

    # accrued -> invoiced -> settled
    status: Mapped[str] = mapped_column(String(20), default="accrued", index=True)
    settled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    notes: Mapped[Optional[str]] = mapped_column(Text)


class Payment(TimestampMixin, Base):
    """Money actually moved. Allocated across one or more invoices."""

    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    payer_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("parties.id", ondelete="SET NULL"), index=True
    )
    payee_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("parties.id", ondelete="SET NULL"), index=True
    )
    paid_on: Mapped[Optional[date]] = mapped_column(Date, index=True)
    amount: Mapped[float] = mapped_column(MONEY, default=0)
    method: Mapped[Optional[str]] = mapped_column(String(24))  # neft|rtgs|upi|cheque|cash
    reference: Mapped[Optional[str]] = mapped_column(String(120))
    bank_name: Mapped[Optional[str]] = mapped_column(String(120))
    notes: Mapped[Optional[str]] = mapped_column(Text)

    allocations: Mapped[list["PaymentAllocation"]] = relationship(
        back_populates="payment", cascade="all, delete-orphan"
    )


class PaymentAllocation(Base):
    __tablename__ = "payment_allocations"

    id: Mapped[int] = mapped_column(primary_key=True)
    payment_id: Mapped[int] = mapped_column(
        ForeignKey("payments.id", ondelete="CASCADE"), index=True
    )
    invoice_id: Mapped[int] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"), index=True
    )
    amount: Mapped[float] = mapped_column(MONEY, default=0)

    payment: Mapped[Payment] = relationship(back_populates="allocations")


class Shipment(TimestampMixin, Base):
    """Physical movement against an invoice."""

    __tablename__ = "shipments"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"), index=True
    )
    transporter_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("parties.id", ondelete="SET NULL"), index=True
    )
    vehicle_no: Mapped[Optional[str]] = mapped_column(String(20), index=True)
    driver_name: Mapped[Optional[str]] = mapped_column(String(120))
    driver_phone: Mapped[Optional[str]] = mapped_column(String(40))
    lr_number: Mapped[Optional[str]] = mapped_column(String(60))
    dispatched_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    freight_amount: Mapped[Optional[float]] = mapped_column(MONEY)
    # pending -> in_transit -> delivered -> short_delivered
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    notes: Mapped[Optional[str]] = mapped_column(Text)


# ===========================================================================
# Background jobs
# ===========================================================================


class Job(Base):
    """Work queue for extraction, so an upload returns instantly."""

    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(40), default="extract", index=True)
    document_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    payload: Mapped[Optional[dict]] = mapped_column(JSON)
    # queued -> running -> done | failed
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
