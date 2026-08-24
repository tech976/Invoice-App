"""Template for `tests/local_bills.py`. Copy it and point at your own bills.

The repository ships synthetic fixtures. Real bills are third-party business
documents, so they are not committed, and neither are the values printed on
them. Tests that need a real document skip when this file is absent.

    cp tests/local_bills.example.py tests/local_bills.py
"""
from __future__ import annotations

from pathlib import Path

from tests.fixtures import BROKEN_FONT, CRYSTAL_REPORTS, TALLY_GST

BILLS_DIR = Path.home() / "Downloads"

# Map each layout slug to a PDF you have locally:
#   TALLY_GST       a clean TallyPrime bill, intra-state, several line items
#   BROKEN_FONT     a bill whose embedded text is mojibake (tests the OCR route)
#   CRYSTAL_REPORTS a Crystal Reports bill with a packing/labour charge
BILL_FILES = {
    TALLY_GST: "your-tally-bill.pdf",
    BROKEN_FONT: "your-scanned-or-broken-font-bill.pdf",
    CRYSTAL_REPORTS: "your-crystal-reports-bill.pdf",
}

# Values printed on those PDFs, used by the layout-anchoring tests. Give the
# figures exactly as your bills carry them; dates as ISO YYYY-MM-DD.
LAYOUT_EXPECTATIONS = {
    TALLY_GST: [
        ("invoice number", "ABC/000001/26-27"),
        ("invoice date", "2026-07-21"),
        ("taxable value", "100000.00"),
        ("grand total", "105000.00"),
    ],
    CRYSTAL_REPORTS: [
        ("invoice number", "00001"),
        ("invoice date", "2026-07-24"),
        ("grand total", "105000.00"),
    ],
}


def path_for(slug: str) -> Path | None:
    name = BILL_FILES.get(slug)
    if not name:
        return None
    candidate = BILLS_DIR / name
    return candidate if candidate.exists() else None
