"""Label anchoring — the foundation of template extraction.

A bill's values change on every issue; its printed labels do not. So a field
is recorded by the label beside it, never by a coordinate. These tests assert
the two properties that make that safe:

  * an anchor must point at a *label*, never at another bill's figure;
  * reading the anchor back must return the value it was learned from.

They run against real PDFs, which are not in the repository — see
`tests/local_bills.example.py`. Without them the file-backed tests skip; the
pure-logic tests below still run.
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

import pytest

from app.extraction.layout import (
    find_anchor,
    indian_group,
    locate_value,
    read_at_anchor,
    read_layout,
    value_candidates,
)
from tests.fixtures import CRYSTAL_REPORTS, TALLY_GST

try:
    from tests import local_bills
except ImportError:
    local_bills = None

_NUMERIC = re.compile(r"^[\(\)\-+]?[\d,.\s]+%?$")


def layout_for(slug):
    if local_bills is None:
        pytest.skip("tests/local_bills.py not configured — no sample bills")
    path = local_bills.path_for(slug)
    if path is None:
        pytest.skip(f"sample bill for '{slug}' not found")
    pages = read_layout(path)
    if not pages:
        pytest.skip(f"no text layer in the bill for '{slug}'")
    return pages


def expectations(slug):
    if local_bills is None:
        pytest.skip("tests/local_bills.py not configured")
    fields = local_bills.LAYOUT_EXPECTATIONS.get(slug)
    if not fields:
        pytest.skip(f"no layout expectations recorded for '{slug}'")
    out = []
    for label, raw in fields:
        if "-" in raw and raw[0].isdigit() and len(raw) == 10:
            out.append((label, date.fromisoformat(raw)))
        elif re.fullmatch(r"[\d.]+", raw):
            out.append((label, Decimal(raw)))
        else:
            out.append((label, raw))
    return out


def roundtrip(pages, value):
    """(anchor, value read back, printed form) for the first form we can find."""
    for candidate in value_candidates(value):
        hit = locate_value(pages, candidate)
        if hit is None:
            continue
        page = next(p for p in pages if p.page_no == hit.page_no)
        anchor = find_anchor(page, hit)
        if anchor is None:
            return None, None, candidate
        return anchor, read_at_anchor(pages, anchor), candidate
    return None, None, None


# --- pure logic, runs anywhere ------------------------------------------


def test_indian_grouping():
    assert indian_group(Decimal("5846893.00")) == "58,46,893.00"
    assert indian_group(Decimal("915216")) == "9,15,216.00"
    assert indian_group(Decimal("0.34")) == "0.34"
    assert indian_group(Decimal("-1234.5")) == "-1,234.50"
    assert indian_group(Decimal("12345678")) == "1,23,45,678.00"


def test_date_forms_cover_what_bills_print():
    forms = value_candidates(date(2026, 7, 21))
    assert "21-Jul-26" in forms      # TallyPrime
    assert "21/07/2026" in forms     # Crystal Reports


# --- against the real documents -----------------------------------------


@pytest.mark.parametrize("slug", [TALLY_GST, CRYSTAL_REPORTS])
def test_every_field_round_trips(slug):
    pages = layout_for(slug)
    for label, value in expectations(slug):
        anchor, got, printed = roundtrip(pages, value)
        assert anchor is not None, f"{label}: no anchor found"
        assert got is not None, f"{label}: anchor read back nothing"
        assert printed.replace(" ", "") in got.replace(" ", ""), (
            f"{label}: anchored on {anchor.label!r} but read back {got!r}, "
            f"expected to contain {printed!r}"
        )


@pytest.mark.parametrize("slug", [TALLY_GST, CRYSTAL_REPORTS])
def test_anchors_are_labels_not_figures(slug):
    """The property that makes a template survive the next bill.

    Anchoring to a neighbouring figure works perfectly on the bill it was
    learned from and fails on every later one, because that figure changes.
    """
    pages = layout_for(slug)
    for label, value in expectations(slug):
        anchor, _, _ = roundtrip(pages, value)
        assert anchor is not None, f"{label}: no anchor"
        assert not _NUMERIC.match(anchor.label.strip()), (
            f"{label} anchored to the figure {anchor.label!r}, which changes "
            "on every bill"
        )


def test_repeated_label_resolves_to_the_right_row():
    """The Crystal Reports bill prints 'Total' twice: line items, then payable."""
    pages = layout_for(CRYSTAL_REPORTS)

    _, subtotal, _ = roundtrip(pages, Decimal("871633.95"))
    grand_anchor, grand, _ = roundtrip(pages, Decimal("915216.00"))

    assert "871,633.95" in subtotal
    assert "915,216.00" in grand
    assert grand_anchor.occurrence == 1, "the payable total is the second 'Total'"


def test_cell_run_stops_at_the_next_cell():
    """'Invoice No : 14593 / 2026-27   Invoice Dt : 24/07/2026' is four cells."""
    pages = layout_for(CRYSTAL_REPORTS)
    _, got, _ = roundtrip(pages, "14593")
    assert "14593" in got
    assert "Invoice Dt" not in got
