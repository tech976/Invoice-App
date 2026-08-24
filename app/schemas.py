"""The extraction contract.

Every invoice format — TallyPrime, Crystal Reports, Busy, Marg, a scanned
photo — is normalised into `ExtractedInvoice`. Vendor-specific wording is
preserved in `raw_label` fields and `unmapped_fields` so that no information
on the bill is silently dropped.

These models are handed to the Anthropic SDK as a strict JSON schema, so keep
them to plain types: str / float / int / bool / list / nested models.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------
# Party
# --------------------------------------------------------------------------


class ExtractedParty(BaseModel):
    """A company on the bill: seller, buyer, consignee or transporter."""

    name: Optional[str] = Field(
        None, description="Company name exactly as printed on the bill."
    )
    gstin: Optional[str] = Field(
        None,
        description="15-character GSTIN/UIN, uppercase, no spaces. Null if absent.",
    )
    pan: Optional[str] = Field(None, description="10-character PAN. Null if absent.")
    fssai: Optional[str] = Field(None, description="FSSAI licence number if printed.")
    address: Optional[str] = Field(
        None,
        description="Full address as printed, newlines replaced by ', '.",
    )
    city: Optional[str] = None
    state_name: Optional[str] = None
    state_code: Optional[str] = Field(
        None, description="Two-digit GST state code, e.g. '27' for Maharashtra."
    )
    pincode: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None


# --------------------------------------------------------------------------
# Line items
# --------------------------------------------------------------------------


class ExtractedLine(BaseModel):
    """One goods/service row of the invoice table."""

    line_no: Optional[int] = Field(None, description="Serial number of the row.")
    description: Optional[str] = Field(
        None, description="Product name as printed, e.g. 'Walnuts Inshell'."
    )
    item_code: Optional[str] = Field(
        None,
        description="Vendor's internal item/SKU code if a separate column exists, "
        "e.g. 'FG000032'. Null if the bill has no code column.",
    )
    item_remarks: Optional[str] = Field(
        None,
        description="Grade / size / variety qualifier printed beside the item, "
        "e.g. '30-34', '36+', 'Solitaire Choco'.",
    )
    brand: Optional[str] = Field(
        None, description="Brand or origin mark printed under the item, e.g. 'Andesfood'."
    )
    hsn: Optional[str] = Field(
        None, description="HSN/SAC code, digits only, dots removed. e.g. '08023100'."
    )
    bags: Optional[float] = Field(
        None,
        description="Number of bags/packages/cartons for this row if printed "
        "(may appear as a 'Bag Qty' column or as text like '200 Bags').",
    )
    quantity: Optional[float] = Field(None, description="Billed quantity as a number.")
    uom: Optional[str] = Field(None, description="Unit of measure, e.g. 'KGS', 'PCS', 'BOX'.")
    rate: Optional[float] = Field(None, description="Rate/price per unit before discount.")
    rate_uom: Optional[str] = Field(None, description="Unit the rate applies to, e.g. 'KGS'.")
    discount_pct: Optional[float] = Field(
        None, description="Discount percentage on this row, as a number (1.5 not '1.50%')."
    )
    discount_amount: Optional[float] = Field(
        None, description="Discount value in rupees on this row, if printed."
    )
    taxable_amount: Optional[float] = Field(
        None,
        description="Net amount for this row after discount, before tax. This is "
        "normally the value in the 'Amount' column.",
    )
    tax_rate: Optional[float] = Field(
        None, description="Total GST rate on this row as a number, e.g. 5 for 5%."
    )
    cgst_amount: Optional[float] = None
    sgst_amount: Optional[float] = None
    igst_amount: Optional[float] = None
    cess_amount: Optional[float] = None
    line_total: Optional[float] = Field(
        None, description="Row total including tax, only if the bill prints one."
    )


# --------------------------------------------------------------------------
# Charges, taxes, e-way bill
# --------------------------------------------------------------------------


class ExtractedCharge(BaseModel):
    """A non-goods amount: freight, packing, handling, discount, round-off, TCS."""

    label: str = Field(description="Label exactly as printed, e.g. 'HANDLING CHARGE'.")
    kind: Literal[
        "discount",
        "packing",
        "labour",
        "freight",
        "handling",
        "insurance",
        "round_off",
        "tcs",
        "tds",
        "other",
    ] = Field(description="Best-fit category for the charge.")
    amount: float = Field(
        description="Amount in rupees. Negative for deductions such as discounts."
    )
    hsn: Optional[str] = None
    tax_rate: Optional[float] = None


class ExtractedTaxSummaryRow(BaseModel):
    """One row of the HSN-wise tax summary table at the foot of the bill."""

    hsn: Optional[str] = None
    taxable_value: Optional[float] = None
    cgst_rate: Optional[float] = None
    cgst_amount: Optional[float] = None
    sgst_rate: Optional[float] = None
    sgst_amount: Optional[float] = None
    igst_rate: Optional[float] = None
    igst_amount: Optional[float] = None
    cess_amount: Optional[float] = None
    total_tax: Optional[float] = None


class ExtractedEwayBill(BaseModel):
    """e-Way bill annexure, usually the last page of the PDF."""

    eway_bill_no: Optional[str] = None
    generated_date: Optional[str] = Field(None, description="ISO date YYYY-MM-DD.")
    generated_by: Optional[str] = Field(None, description="GSTIN that generated it.")
    valid_upto: Optional[str] = Field(None, description="ISO date YYYY-MM-DD.")
    mode: Optional[str] = Field(None, description="e.g. 'Road', 'Rail', 'Air', 'Ship'.")
    approx_distance_km: Optional[float] = None
    supply_type: Optional[str] = None
    transaction_type: Optional[str] = None
    dispatch_from: Optional[str] = None
    ship_to: Optional[str] = None
    transporter_id: Optional[str] = Field(None, description="Transporter GSTIN / TRANSIN.")
    transporter_name: Optional[str] = None
    transporter_doc_no: Optional[str] = None
    transporter_doc_date: Optional[str] = Field(None, description="ISO date YYYY-MM-DD.")
    vehicle_no: Optional[str] = Field(None, description="e.g. 'MH04DS4070', no spaces.")
    vehicle_from: Optional[str] = None
    cewb_no: Optional[str] = None


class ExtractedBank(BaseModel):
    """Seller's bank details printed on the bill."""

    account_holder: Optional[str] = None
    bank_name: Optional[str] = None
    account_number: Optional[str] = None
    ifsc: Optional[str] = None
    branch: Optional[str] = None


class UnmappedField(BaseModel):
    """Anything printed on the bill that has no home in this schema.

    This is what keeps unfamiliar formats loss-free — capture it here rather
    than dropping it.
    """

    label: str = Field(description="Label as printed on the bill.")
    value: str = Field(description="Value as printed.")
    section: Optional[str] = Field(
        None, description="Where on the bill it appeared, e.g. 'header', 'footer'."
    )


class FieldNote(BaseModel):
    """A field the model was unsure about, so a human can check it fast."""

    field_path: str = Field(
        description="Dotted path into this object, e.g. 'lines.0.rate' or 'grand_total'."
    )
    issue: str = Field(description="Why it is uncertain: illegible, ambiguous, inferred...")
    printed_text: Optional[str] = Field(
        None, description="The raw text you read for it, if any."
    )


# --------------------------------------------------------------------------
# The invoice
# --------------------------------------------------------------------------


class ExtractedInvoice(BaseModel):
    """A complete normalised invoice, as read off one uploaded document."""

    # -- document identity --------------------------------------------
    document_type: Literal[
        "tax_invoice",
        "proforma_invoice",
        "bill_of_supply",
        "credit_note",
        "debit_note",
        "delivery_challan",
        "purchase_order",
        "other",
    ] = Field(description="What kind of document this is, from its own heading.")
    invoice_number: Optional[str] = Field(
        None, description="Invoice number exactly as printed, e.g. 'WHL/001634/26-27'."
    )
    invoice_date: Optional[str] = Field(
        None, description="Invoice date as ISO YYYY-MM-DD. '21-Jul-26' -> '2026-07-21'."
    )
    due_date: Optional[str] = Field(None, description="ISO YYYY-MM-DD if printed or derivable.")
    irn: Optional[str] = Field(None, description="64-character e-invoice IRN hash.")
    ack_no: Optional[str] = Field(None, description="e-invoice acknowledgement number.")
    ack_date: Optional[str] = Field(None, description="ISO YYYY-MM-DD.")
    po_number: Optional[str] = Field(None, description="Buyer's order / PO number.")
    po_date: Optional[str] = Field(None, description="ISO YYYY-MM-DD.")
    delivery_note: Optional[str] = None
    delivery_note_date: Optional[str] = Field(None, description="ISO YYYY-MM-DD.")

    # -- parties --------------------------------------------------------
    seller: ExtractedParty = Field(
        description="The supplier issuing the bill — the letterhead at the top."
    )
    buyer: ExtractedParty = Field(description="Bill-to party.")
    consignee: Optional[ExtractedParty] = Field(
        None, description="Ship-to party. Null if identical to the buyer."
    )
    broker_name: Optional[str] = Field(
        None,
        description="Broker / commission agent / representative named on the bill. "
        "Look for labels: Broker Name, Representative, Agent, Through, Dalal.",
    )

    # -- commercial terms ------------------------------------------------
    place_of_supply: Optional[str] = None
    payment_terms: Optional[str] = Field(
        None, description="e.g. '7 Days', '30 Days', 'Advance'."
    )
    currency: str = Field("INR", description="ISO currency code.")

    # -- body -----------------------------------------------------------
    lines: list[ExtractedLine] = Field(
        default_factory=list, description="Every goods row, in printed order."
    )
    charges: list[ExtractedCharge] = Field(
        default_factory=list,
        description="Non-goods amounts: discount, packing, freight, round-off, TCS.",
    )
    tax_summary: list[ExtractedTaxSummaryRow] = Field(
        default_factory=list, description="HSN-wise tax table rows, if the bill has one."
    )

    # -- totals ---------------------------------------------------------
    total_quantity: Optional[float] = Field(None, description="Sum-of-quantity if printed.")
    total_quantity_uom: Optional[str] = None
    total_bags: Optional[float] = None
    subtotal: Optional[float] = Field(
        None, description="Sum of line amounts before charges and tax."
    )
    discount_total: Optional[float] = Field(None, description="Total discount, positive number.")
    taxable_value: Optional[float] = Field(
        None, description="Total taxable value the tax is computed on."
    )
    cgst_amount: Optional[float] = None
    sgst_amount: Optional[float] = None
    igst_amount: Optional[float] = None
    cess_amount: Optional[float] = None
    tcs_amount: Optional[float] = None
    other_charges: Optional[float] = Field(
        None, description="Total of freight/packing/handling type charges."
    )
    round_off: Optional[float] = Field(None, description="Round-off, may be negative.")
    grand_total: Optional[float] = Field(
        None, description="Final payable amount — the single most important number."
    )
    amount_in_words: Optional[str] = None

    # -- attachments -----------------------------------------------------
    eway_bill: Optional[ExtractedEwayBill] = None
    bank: Optional[ExtractedBank] = None
    remarks: Optional[str] = Field(None, description="Free-text remarks/notes on the bill.")
    terms: Optional[str] = Field(None, description="Terms & conditions block, if present.")

    # -- provenance ------------------------------------------------------
    unmapped_fields: list[UnmappedField] = Field(
        default_factory=list,
        description="Printed labels/values that do not fit any field above.",
    )
    field_notes: list[FieldNote] = Field(
        default_factory=list,
        description="Fields you are NOT confident about. Be honest — a flagged "
        "field gets checked by a human, a wrongly-confident one does not.",
    )
    overall_confidence: float = Field(
        description="0.0-1.0 confidence that the whole extraction is correct."
    )
    vendor_format_hint: Optional[str] = Field(
        None,
        description="Short slug naming the bill layout, e.g. 'tally-prime-gst' or "
        "'crystal-reports-ashapura'. Used to group similar formats.",
    )
