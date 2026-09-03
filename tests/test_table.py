"""Row classification — telling goods from charges from the totals strip.

The rows under a bill's table header are not all goods. Beneath them sits the
totals strip, and its labels are right-aligned against the amount column, so
they land under whichever column happens to sit there — 'rate' on one layout,
'rate_uom' on the next, nothing predictable.

That is what these tests pin down: a label must be readable wherever it falls,
a restated total must not be booked as a charge, and a figure the reader
cannot name must not be invented into one.
"""
from __future__ import annotations

import pytest

from app.extraction.local.table import RawRow, classify


def row(**cells) -> RawRow:
    return RawRow(cells=cells)


def test_label_is_read_whatever_column_it_lands_in():
    """A totals label right-aligned against the amount lands off to the right.

    Reading only the description columns left these rows with no label at all,
    which is how a printed charge became anonymous.
    """
    assert row(rate="Packing & Forwarding", amount="12,500.00").full_text == \
        "Packing & Forwarding"
    assert row(rate_uom="Round Off", amount="-0.50").full_text == "Round Off"
    assert row(quantity="Freight", amount="8,000.00").full_text == "Freight"


def test_figures_are_not_part_of_a_label():
    """Otherwise every bare amount acquires a 'label' made of its own digits."""
    assert row(serial="1", description="Toor Dal", quantity="150.00",
               rate="9,850.00", amount="14,77,500.00").full_text == "Toor Dal"
    assert row(discount="2.5%", amount="1,000.00").full_text == ""


@pytest.mark.parametrize("label", [
    "Taxable Value", "Taxable Amount", "Sub Total", "Gross Value",
    "Total Value", "Net Amount", "Grand Total", "Invoice Value",
])
def test_a_restated_total_is_not_a_charge(label):
    """The taxable value is already a field of its own.

    Booking it again as a charge counts the same printed rupee twice, and the
    doubled total then fails the grand-total check on a bill whose arithmetic
    is sound.
    """
    assert classify(row(rate=label, amount="20,71,100.00")) == ("total", None)


def test_a_named_charge_keeps_its_name():
    """'other' is what the reader says when it could not read the label."""
    assert classify(row(rate="Packing & Forwarding", amount="12,500.00")) == \
        ("charge", "packing")
    assert classify(row(rate="Round Off", amount="-0.50")) == ("charge", "round_off")
    assert classify(row(description="Cash Discount", amount="500.00")) == \
        ("charge", "discount")


def test_an_unnamed_amount_is_booked_nowhere():
    """Every real charge names itself — that is how the buyer knows what it is.

    A bare figure is the reader having found a number it cannot account for.
    Calling it a charge doubles it against `other_charges`; calling it goods
    doubles it against the subtotal, since a line with no description and no
    quantity still carries its amount. Neither, and the grand-total check
    speaks up if that left a real gap.
    """
    assert classify(row(amount="20,71,100.00")) == ("total", None)


def test_tax_rows_still_go_to_tax():
    assert classify(row(rate="CGST @ 2.5%", amount="51,777.50")) == ("tax", None)
    assert classify(row(rate="IGST", amount="1,03,555.00")) == ("tax", None)


def test_a_row_with_a_quantity_is_goods():
    """A charge never carries a quantity — that is what separates
    'PACKING & LABOUR 5%' at 825.00 from a goods row priced per kilo."""
    assert classify(row(description="Packing Material", quantity="10.00",
                        rate="1,250.00", amount="12,500.00")) == ("goods", None)
