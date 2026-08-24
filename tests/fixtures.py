"""Reference extractions used across the test suite.

The figures, dates, quantities, rates, tax rates and HSN codes here are taken
verbatim from three real bills, because the point of these fixtures is to
prove the arithmetic reconciles on documents that actually exist. The
*identities* are synthetic: company names, addresses, phone numbers, bank
accounts, GSTINs, IRNs and e-way bill numbers were all replaced, since the
real ones belong to third-party suppliers and this repository is public.

The synthetic GSTINs carry correct mod-36 check digits, so
`test_misread_gstin_character_is_caught` still exercises the real validator.

Filenames of the local PDFs live in `tests/local_bills.py`, which is not
committed. Tests needing a real document skip when it is absent.
"""
from __future__ import annotations

from app.schemas import (
    ExtractedBank,
    ExtractedCharge,
    ExtractedEwayBill,
    ExtractedInvoice,
    ExtractedLine,
    ExtractedParty,
    ExtractedTaxSummaryRow,
    UnmappedField,
)

# Slugs the tests and the demo seeder use to refer to a bill layout.
TALLY_GST = "tally-gst"          # clean text layer, intra-state, 3 line items
BROKEN_FONT = "broken-font"      # text layer is mojibake, inter-state
CRYSTAL_REPORTS = "crystal"      # clean text layer, packing charge taxed

# --------------------------------------------------------------------------
# 1. Northgate Agro -> Riverstone Impex.  Intra-state, CGST+SGST, 3 grades.
# --------------------------------------------------------------------------

RIVERSTONE = ExtractedParty(
    name="Riverstone Impex Private Limited B-12",
    gstin="27RVSTL7392R1ZI",
    address=("B-12, RIVERSTONE IMPEX PRIVATE LIMITED, APMC MARKET-1, "
             "PHASE-2, SECTOR-19, Vashi, Navi Mumbai, Thane, Maharashtra, 400703"),
    city="Navi Mumbai",
    state_name="Maharashtra",
    state_code="27",
    pincode="400703",
)

TALLY_INVOICE = ExtractedInvoice(
    document_type="tax_invoice",
    invoice_number="NGA/001634/26-27",
    invoice_date="2026-07-21",
    irn="3c41ba7e58d2409fbe17c05a9d6413882af0e7cb15d94a67e2fb830c47591dae",
    ack_no="112600000000001",
    ack_date="2026-07-21",
    seller=ExtractedParty(
        name="Northgate Agro Commodities Limited",
        gstin="27NGACL2841M1ZO",
        pan="NGACL2841M",
        fssai="10000000000001",
        address=("Shop No. G-17, APMC Market-I, Phase-II, Masala Market, "
                 "Sec-19, Vashi, Navi Mumbai, 400703"),
        city="Navi Mumbai",
        state_name="Maharashtra",
        state_code="27",
        pincode="400703",
        phone="+91 - 9000000001",
        email="accounts@northgateagro.example",
    ),
    buyer=RIVERSTONE,
    consignee=RIVERSTONE,
    broker_name="Ramesh Kulkarni",
    payment_terms="7 Days",
    lines=[
        ExtractedLine(
            line_no=1, description="Walnuts Inshell", item_remarks="30-34",
            brand="Andesfood", hsn="08023100", bags=200, quantity=5000, uom="KGS",
            rate=648.57, rate_uom="KGS", discount_pct=1, taxable_amount=3210422.00,
        ),
        ExtractedLine(
            line_no=2, description="Walnuts Inshell", item_remarks="34-36",
            brand="Andesfood", hsn="08023100", bags=100, quantity=2500, uom="KGS",
            rate=696.19, rate_uom="KGS", discount_pct=1, taxable_amount=1723071.00,
        ),
        ExtractedLine(
            line_no=3, description="Walnuts Inshell", item_remarks="36+",
            brand="Andesfood", hsn="08023100", bags=50, quantity=1250, uom="KGS",
            rate=738.10, rate_uom="KGS", discount_pct=1, taxable_amount=913400.00,
        ),
    ],
    charges=[ExtractedCharge(label="R/OFF", kind="round_off", amount=0.34)],
    tax_summary=[
        ExtractedTaxSummaryRow(
            hsn="08023100", taxable_value=5846893.00,
            cgst_rate=2.5, cgst_amount=146172.33,
            sgst_rate=2.5, sgst_amount=146172.33, total_tax=292344.66,
        )
    ],
    total_quantity=8750, total_quantity_uom="KGS", total_bags=350,
    subtotal=5846893.00, taxable_value=5846893.00,
    cgst_amount=146172.33, sgst_amount=146172.33,
    round_off=0.34, grand_total=6139238.00,
    amount_in_words=("INR Sixty One Lakh Thirty Nine Thousand Two Hundred "
                     "Thirty Eight Only."),
    eway_bill=ExtractedEwayBill(
        eway_bill_no="100000000001",
        generated_date="2026-07-21",
        generated_by="27NGACL2841M1ZO",
        valid_upto="2026-07-22",
        mode="Road",
        approx_distance_km=2,
        supply_type="Outward-Supply",
        transaction_type="Bill From - Dispatch From",
        dispatch_from="G-17, Apmc Masala Market, Vashi, Navi Mumbai -400703",
        ship_to="B-12, RIVERSTONE IMPEX, APMC MARKET-1, PHASE-2, SECTOR-19, Vashi",
        vehicle_no="MH04AA1001",
        vehicle_from="Vashi",
    ),
    bank=ExtractedBank(
        bank_name="Example Bank Retail",
        account_number="000000000001",
        ifsc="EXMP0000001",
        branch="Turbe MIDC Road",
    ),
    unmapped_fields=[
        UnmappedField(label="Mode/Terms of Payment", value="7 Days", section="header"),
    ],
    overall_confidence=0.97,
    vendor_format_hint="tally-prime-gst",
)

# --------------------------------------------------------------------------
# 2. K.R.FOODS -> Sunrise Traders.  The bill whose PDF text layer is mojibake.
#    Inter-state; discount and handling charge sit outside the goods rows.
# --------------------------------------------------------------------------

SUNRISE = ExtractedParty(
    name="Sunrise Traders",
    gstin="29SNRTB4426N2ZQ",
    address="No.199, 6th Main Road, Apmc Yard, Yeshwanthpur, Bengaluru Urban",
    city="Bengaluru",
    state_name="Karnataka",
    state_code="29",
    pincode="560022",
)

BROKEN_FONT_INVOICE = ExtractedInvoice(
    document_type="tax_invoice",
    invoice_number="433",
    invoice_date="2026-07-22",
    irn="8b2fd6104ae7395c2b81f0da4c6e7739105bd82ae43f6019cb7d25a83f0e614c",
    ack_no="112600000000002",
    ack_date="2026-07-22",
    seller=ExtractedParty(
        name="K.R.FOODS",
        gstin="27KRFPJ5107E1ZU",
        address="PLOT NO.C-451, TTC INDUSTRIAL AREA, PAWANE MIDC ROAD, NAVI MUMBAI, THANE",
        city="Navi Mumbai",
        state_name="Maharashtra",
        state_code="27",
        pincode="400705",
    ),
    buyer=SUNRISE,
    consignee=SUNRISE,
    lines=[
        ExtractedLine(
            line_no=1, description="Almond Kernels", hsn="08021200",
            quantity=1050, uom="KGS", rate=813.00, rate_uom="KGS",
            taxable_amount=853650.00, tax_rate=5,
        )
    ],
    charges=[
        ExtractedCharge(label="DISCOUNT", kind="discount", amount=-12804.75, tax_rate=1.5),
        ExtractedCharge(label="HANDLING CHARGE", kind="handling", amount=1575.00),
        ExtractedCharge(label="ROUND OFF", kind="round_off", amount=0.49),
    ],
    tax_summary=[
        ExtractedTaxSummaryRow(
            hsn="08021200", taxable_value=840845.25,
            igst_rate=5, igst_amount=42042.26, total_tax=42042.26,
        )
    ],
    total_quantity=1050, total_quantity_uom="KGS",
    subtotal=853650.00, discount_total=12804.75, taxable_value=840845.25,
    igst_amount=42042.26, other_charges=1575.00, round_off=0.49,
    grand_total=884463.00,
    amount_in_words="INR Eight Lakh Eighty Four Thousand Four Hundred Sixty Three Only",
    remarks="REF/70 BAG",
    eway_bill=ExtractedEwayBill(
        eway_bill_no="100000000002",
        generated_date="2026-07-22",
        generated_by="27KRFPJ5107E1ZU",
        valid_upto="2026-07-27",
        mode="Road",
        approx_distance_km=956,
        supply_type="Outward-Supply",
        transaction_type="Regular",
        transporter_id="29VEGPM3384H1ZL",
        transporter_name="Vega Cargo Movers",
        vehicle_no="MH04BB2002",
        vehicle_from="Navi Mumbai",
    ),
    bank=ExtractedBank(
        account_holder="K.R.FOODS.MUMBAI",
        bank_name="EXAMPLE BANK",
        account_number="000000000002",
        ifsc="EXMP0000002",
        branch="MODEL TOWN",
    ),
    overall_confidence=0.88,
    vendor_format_hint="tally-prime-krfoods",
)

# --------------------------------------------------------------------------
# 3. Bluepeak Agrocomm -> Sunrise Traders.  Crystal Reports. Packing charge
#    taxed with the goods, and the round-off is implied rather than printed.
# --------------------------------------------------------------------------

CRYSTAL_INVOICE = ExtractedInvoice(
    document_type="tax_invoice",
    invoice_number="14593 / 2026-27",
    invoice_date="2026-07-24",
    irn="d70a1c9e4f6b28035ea9174cb35d8206ff41e9a7c0b562d83179e4a06cb2513f",
    seller=ExtractedParty(
        name="Bluepeak Agrocomm Pvt Ltd",
        gstin="27BLPKA6015E1ZN",
        pan="BLPKA6015E",
        fssai="10000000000003",
        address=("B Wing Corporate Aura 1803 To 1806 Thane Belapur Road Turbhe, "
                 "Navi Mumbai-400705, Maharashtra, India"),
        city="Navi Mumbai",
        state_name="Maharashtra",
        state_code="27",
        pincode="400705",
        phone="+91-22-40000003",
        email="accounts@bluepeakagro.example",
    ),
    buyer=ExtractedParty(
        name="SUNRISE TRADERS -Karnataka",
        gstin="29SNRTB4426N2ZQ",
        pan="SNRTB4426N",
        address="199 6th Main Road, APMC YARD YESHWANTPUR, Bengaluru, Karnataka",
        city="Bengaluru",
        state_name="Karnataka",
        state_code="29",
        pincode="560022",
        phone="9000000003",
    ),
    consignee=ExtractedParty(
        name="SUNRISE TRADERS -Karnataka",
        gstin="29SNRTB4426N2ZQ",
        state_name="Karnataka",
        state_code="29",
    ),
    broker_name="Suresh Deshmukh ( Suresh C 12 )",
    place_of_supply="Karnataka",
    lines=[
        ExtractedLine(
            line_no=1, description="Almonds - Solitaire Choco",
            item_code="FG000032", item_remarks="Solitaire Choco", hsn="0802.1200",
            bags=33, quantity=990, uom="KGS", rate=893.00, rate_uom="KGS",
            discount_pct=1.5, taxable_amount=870808.95, tax_rate=5,
            igst_amount=43540.45,
        )
    ],
    charges=[
        ExtractedCharge(
            label="PACKING & LABOUR 5%", kind="packing",
            amount=825.00, hsn="08013220", tax_rate=5,
        )
    ],
    total_quantity=990, total_quantity_uom="KGS", total_bags=33,
    subtotal=870808.95, taxable_value=871633.95,
    igst_amount=43581.70, grand_total=915216.00,
    amount_in_words="INR Nine lakhs Fifteen Thousand Two Hundred Sixteen only",
    remarks=("INCASE PAYMENT THROUGH NEFT/RTGS MODE, PLEASE SHARE THE PAYMENT "
             "DETAILS ON 9000000004 THROUGH WHATSAPP/SMS."),
    bank=ExtractedBank(
        account_holder="Bluepeak Agrocomm Pvt Ltd",
        bank_name="Example Bank",
        account_number="000000000003",
        ifsc="EXMP0000003",
        branch="Vashi / Navi Mumbai",
    ),
    unmapped_fields=[
        UnmappedField(label="Ship From", value="K15 SECTOR-19 NAVI MUMBAI - 400703", section="header"),
        UnmappedField(label="Tax Amt In Words",
                      value="INR Forty-Three Thousand Five Hundred Eighty-One Rupees and Seventy only",
                      section="footer"),
    ],
    overall_confidence=0.93,
    vendor_format_hint="crystal-reports-bluepeak",
)

# Keyed by layout slug. `tests/local_bills.py` maps each slug to the PDF on
# this machine; without it, tests needing a real document skip.
SAMPLES = {
    TALLY_GST: TALLY_INVOICE,
    BROKEN_FONT: BROKEN_FONT_INVOICE,
    CRYSTAL_REPORTS: CRYSTAL_INVOICE,
}
