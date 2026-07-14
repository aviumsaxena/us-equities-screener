"""Tests for universe resolution.

~960 of SEC's ~7,600 filers list more than one share class under one CIK, and
`companies.cik` is UNIQUE — so which class we keep is a real decision, not a
detail. Getting it wrong swaps GOOGL for GOOG, and BRK-B for BRK-A (a ~1,500x
different share price, which would poison every price-derived metric).
"""
from __future__ import annotations

from etl.tickers import primary_by_cik


def _entry(cik: int, ticker: str, title: str = "Co") -> dict:
    return {"cik_str": cik, "ticker": ticker, "title": title}


def test_keeps_secs_first_listing_not_the_shortest_ticker():
    # SEC lists the primary/most-liquid class first
    entries = [
        _entry(1652044, "GOOGL", "Alphabet Inc."),
        _entry(1652044, "GOOG", "Alphabet Inc."),
        _entry(1652044, "GOOGN", "Alphabet Inc."),
    ]
    assert primary_by_cik(entries)[1652044]["ticker"] == "GOOGL"


def test_berkshire_keeps_the_retail_class():
    # BRK-A trades ~1,500x BRK-B; picking it would wreck price-derived metrics
    entries = [_entry(1067983, "BRK-B"), _entry(1067983, "BRK-A")]
    assert primary_by_cik(entries)[1067983]["ticker"] == "BRK-B"


def test_one_row_per_cik():
    entries = [
        _entry(1, "AAA"),
        _entry(1, "AAAB"),
        _entry(2, "BBB"),
    ]
    primary = primary_by_cik(entries)
    assert set(primary) == {1, 2}


def test_single_class_filer_is_unchanged():
    entries = [_entry(320193, "AAPL", "Apple Inc.")]
    assert primary_by_cik(entries)[320193]["ticker"] == "AAPL"
