"""Tests for the SIC -> sector mapping.

The ranges in etl/sic.py are evaluated in order, so a narrow range that must
beat a broader one (pharma inside chemicals, drug stores inside retail,
computers inside machinery) only works if it is listed first. These tests pin
those precedence rules -- reordering the table breaks them loudly instead of
silently reclassifying half the universe.
"""
from __future__ import annotations

import pytest

from etl.sic import sic_to_sector


@pytest.mark.parametrize(
    "sic, expected, why",
    [
        # precedence: narrower range must win over the broader one that contains it
        ("2834", "Health Care", "pharma sits inside the 28xx chemicals->Materials block"),
        ("2840", "Consumer Staples", "soap/cosmetics sits inside the 28xx chemicals block"),
        ("2911", "Energy", "petroleum refining sits inside the 29xx range"),
        ("3571", "Information Technology", "computers sit inside 34xx-35xx machinery->Industrials"),
        ("3826", "Health Care", "lab instruments sit inside 38xx instruments->Industrials"),
        ("5912", "Consumer Staples", "drug stores sit inside 59xx misc retail->Cons. Disc."),
        ("5331", "Consumer Staples", "variety stores (WMT) are Staples, not Cons. Disc."),
        # representative sample-universe assignments
        ("3674", "Information Technology", "semiconductors (NVDA)"),
        ("7372", "Information Technology", "prepackaged software (MSFT)"),
        ("6021", "Financials", "national commercial banks (JPM)"),
        ("6331", "Financials", "insurance (BRK-B)"),
        ("3711", "Consumer Discretionary", "motor vehicles (TSLA)"),
        ("5961", "Consumer Discretionary", "catalog/mail-order retail (AMZN)"),
        ("5211", "Consumer Discretionary", "building materials retail (HD)"),
        ("2080", "Consumer Staples", "beverages (KO/PEP)"),
        # neighbours that must NOT bleed into each other
        ("3720", "Industrials", "aerospace is Industrials, not Cons. Disc. like autos"),
        ("6798", "Real Estate", "REITs split out of Financials"),
        ("4911", "Utilities", "electric utilities"),
    ],
)
def test_sic_maps_to_expected_sector(sic, expected, why):
    assert sic_to_sector(sic) == expected, why


@pytest.mark.parametrize("bad", [None, "", "abc", "99999999"])
def test_unmapped_sic_returns_none(bad):
    # a NULL sector beats a confidently wrong one
    assert sic_to_sector(bad) is None
