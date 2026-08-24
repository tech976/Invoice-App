"""Validation rules exercised against real invoice arithmetic.

The figures are transcribed by hand from three real bills, so a passing test
means the rules accept genuine invoices; the mutation tests mean they still
catch a bad one. Company names and GSTINs are synthetic — see
`tests/fixtures.py` for why — but the check digits are correct, so the GSTIN
test exercises the real validator.
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace as NS

from app.validation.rules import ERROR, validate_invoice


def party(gstin, state_code, name="X"):
    return NS(gstin=gstin, state_code=state_code, legal_name=name)


def line(no, desc, qty, rate, disc, amount, hsn="08023100", uom="KGS"):
    return NS(
        line_no=no, description=desc, quantity=qty, rate=rate, discount_pct=disc,
        discount_amount=None, taxable_amount=amount, hsn=hsn, uom=uom,
    )


def charge(label, kind, amount):
    return NS(label=label, kind=kind, amount=amount, hsn=None, tax_rate=None)


def tax_row(hsn, taxable, cgst_r=None, cgst_a=None, sgst_r=None, sgst_a=None,
            igst_r=None, igst_a=None):
    return NS(
        hsn=hsn, taxable_value=taxable, cgst_rate=cgst_r, cgst_amount=cgst_a,
        sgst_rate=sgst_r, sgst_amount=sgst_a, igst_rate=igst_r,
        igst_amount=igst_a, cess_amount=None, total_tax=None,
    )


def invoice(**kw):
    base = dict(
        invoice_number="X/1", invoice_date=date(2026, 7, 21), due_date=None,
        seller_id=1, buyer_id=2, seller=None, buyer=None, consignee=None,
        lines=[], charges=[], tax_rows=[],
        taxable_value=None, cgst_amount=None, sgst_amount=None,
        igst_amount=None, cess_amount=None, tcs_amount=None,
        other_charges=None, round_off=None, grand_total=None,
        total_quantity=None, amount_in_words=None, irn=None, eway_bill_no=None,
    )
    base.update(kw)
    return NS(**base)


# --- the three real bills -------------------------------------------------


def tally_bill():
    """Intra-state (27->27), CGST+SGST, three walnut grades."""
    return invoice(
        invoice_number="NGA/001634/26-27",
        invoice_date=date(2026, 7, 21),
        seller=party("27NGACL2841M1ZO", "27", "Northgate Agro Commodities Limited"),
        buyer=party("27RVSTL7392R1ZI", "27", "Riverstone Impex Private Limited B-12"),
        lines=[
            line(1, "Walnuts Inshell 30-34", 5000, 648.57, 1, 3210422.00),
            line(2, "Walnuts Inshell 34-36", 2500, 696.19, 1, 1723071.00),
            line(3, "Walnuts Inshell 36+", 1250, 738.10, 1, 913400.00),
        ],
        tax_rows=[tax_row("08023100", 5846893.00, 2.5, 146172.33, 2.5, 146172.33)],
        taxable_value=5846893.00,
        cgst_amount=146172.33, sgst_amount=146172.33,
        round_off=0.34, grand_total=6139238.00, total_quantity=8750.0,
        amount_in_words="INR Sixty One Lakh Thirty Nine Thousand Two Hundred Thirty Eight Only.",
        irn="3c41ba7e58d2409fbe17c05a9d6413882af0e7cb15d94a67e2fb830c47591dae",
        eway_bill_no="100000000001",
    )


def broken_font_bill():
    """Inter-state (27->29) IGST, post-tax handling charge."""
    return invoice(
        invoice_number="433",
        invoice_date=date(2026, 7, 22),
        seller=party("27KRFPJ5107E1ZU", "27", "K.R.FOODS"),
        buyer=party("29SNRTB4426N2ZQ", "29", "Sunrise Traders"),
        lines=[line(1, "Almond Kernels", 1050, 813.00, None, 853650.00, hsn="08021200")],
        charges=[
            charge("DISCOUNT", "discount", -12804.75),
            charge("HANDLING CHARGE", "handling", 1575.00),
            charge("ROUND OFF", "round_off", 0.49),
        ],
        tax_rows=[tax_row("08021200", 840845.25, igst_r=5, igst_a=42042.26)],
        taxable_value=840845.25, igst_amount=42042.26,
        other_charges=1575.00, round_off=0.49, grand_total=884463.00,
        total_quantity=1050.0,
        amount_in_words="INR Eight Lakh Eighty Four Thousand Four Hundred Sixty Three Only",
    )


def crystal_bill():
    """Packing charge taxed with the goods, implied round-off."""
    return invoice(
        invoice_number="14593 / 2026-27",
        invoice_date=date(2026, 7, 24),
        seller=party("27BLPKA6015E1ZN", "27", "Bluepeak Agrocomm Pvt Ltd"),
        buyer=party("29SNRTB4426N2ZQ", "29", "SUNRISE TRADERS -Karnataka"),
        lines=[line(1, "Almonds - Solitaire Choco", 990, 893.00, 1.5, 870808.95, hsn="08021200")],
        charges=[charge("PACKING & LABOUR 5%", "packing", 825.00)],
        taxable_value=871633.95, igst_amount=43581.70,
        grand_total=915216.00, total_quantity=990.0,
        amount_in_words="INR Nine lakhs Fifteen Thousand Two Hundred Sixteen only",
    )


REAL_BILLS = {
    "Northgate -> Riverstone": tally_bill,
    "K.R.FOODS -> Sunrise": broken_font_bill,
    "Bluepeak -> Sunrise": crystal_bill,
}


def test_real_bills_have_no_errors():
    for name, builder in REAL_BILLS.items():
        flags = validate_invoice(builder(), confidence=0.95)
        errors = [f for f in flags if f.severity == ERROR]
        assert not errors, f"{name} wrongly flagged: {[f.message for f in errors]}"


def test_digit_grouping_error_is_caught():
    """The classic failure: 58,46,893 read as 5,846,893 the wrong way."""
    inv = tally_bill()
    inv.grand_total = 613923.80  # decimal point slipped
    flags = validate_invoice(inv)
    assert any(f.rule == "amount_in_words_mismatch" for f in flags)


def test_wrong_line_amount_is_caught():
    inv = tally_bill()
    inv.lines[0].taxable_amount = 3120422.00  # two digits transposed
    rules = {f.rule for f in validate_invoice(inv)}
    assert "line_arithmetic" in rules
    assert "taxable_value_mismatch" in rules


def test_igst_on_intrastate_is_caught():
    inv = tally_bill()
    inv.igst_amount = 292344.66
    inv.cgst_amount = inv.sgst_amount = None
    assert any(f.rule == "supply_type_mismatch" for f in validate_invoice(inv))


def test_cgst_on_interstate_is_caught():
    inv = broken_font_bill()
    inv.igst_amount = None
    inv.cgst_amount = inv.sgst_amount = 21021.13
    assert any(f.rule == "supply_type_mismatch" for f in validate_invoice(inv))


def test_misread_gstin_character_is_caught():
    inv = tally_bill()
    inv.seller = party("27NGACL2841M1ZB", "27")  # O misread as B
    assert any(f.rule == "gstin_invalid" for f in validate_invoice(inv))


def test_tax_computed_at_wrong_rate_is_caught():
    inv = tally_bill()
    inv.tax_rows[0].cgst_amount = 292344.66  # 5% booked as CGST instead of 2.5%
    assert any(f.rule == "tax_computation" for f in validate_invoice(inv))


def test_missing_required_fields_are_caught():
    inv = invoice(invoice_number=None, invoice_date=None, grand_total=None)
    rules = [f.rule for f in validate_invoice(inv)]
    assert rules.count("missing_required_field") == 3
    assert "no_line_items" in rules
