"""Normalisation of the values a reading returns.

Day-first parsing is right for what an Indian bill prints, but the extraction
schema asks the model for ISO dates, and the two conventions disagree on
every date whose day is 12 or less.
"""
from __future__ import annotations

from datetime import date

from app.extraction.normalize import financial_year, parse_date


def test_iso_date_is_not_read_day_first():
    """`2026-06-01` is 1 June 2026, not 6 January."""
    assert parse_date("2026-06-01") == date(2026, 6, 1)
    assert parse_date("2026-12-05") == date(2026, 12, 5)


def test_printed_dates_stay_day_first():
    assert parse_date("03/04/2026") == date(2026, 4, 3)
    assert parse_date("1-Jun-26") == date(2026, 6, 1)
    assert parse_date("21-Jul-26") == date(2026, 7, 21)


def test_time_component_is_dropped():
    assert parse_date("21-Jul-26 6:31 PM") == date(2026, 7, 21)


def test_impossible_date_is_rejected():
    assert parse_date("2026-13-45") is None
    assert parse_date("") is None
    assert parse_date(None) is None


def test_financial_year_follows_the_april_boundary():
    """A misread June date would file the bill in the previous year."""
    assert financial_year(parse_date("2026-06-01")) == "2026-27"
    assert financial_year(parse_date("2026-03-31")) == "2025-26"
    assert financial_year(parse_date("2026-04-01")) == "2026-27"
