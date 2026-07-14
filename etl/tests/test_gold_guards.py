"""Gold's numeric guards, built from real failures across the ~7.6k universe.

Postgres NUMERIC(p,s) columns don't quietly truncate an oversized value — they
abort the INSERT. So an unguarded ratio doesn't just store nonsense, it takes
down the whole run. Both guards below exist because the run actually died.
"""
from __future__ import annotations

from decimal import Decimal

from etl.gold.metrics import MAX_PLAUSIBLE_EPS, _null_unstorable, _plausible_eps


# --- mis-tagged EPS (UAMY) ---------------------------------------------------

def test_share_count_mistagged_as_eps_is_rejected():
    # real: United States Antimony tagged EarningsPerShareDiluted with these
    for bogus in (69_697_150, 92_711_336, 107_260_472):
        assert not _plausible_eps(Decimal(bogus))


def test_real_eps_is_kept():
    # ordinary EPS, a loss, and BRK-A's genuinely huge per-share earnings
    for good in ("-0.01", "2.87", "40000"):
        assert _plausible_eps(Decimal(good))


def test_eps_bound_is_above_the_priciest_real_share():
    assert MAX_PLAUSIBLE_EPS > Decimal(40_000)


def test_none_eps_passes_through():
    assert _plausible_eps(None)


# --- ratios that overflow their column ---------------------------------------

def test_absurd_margin_becomes_null_not_a_crash():
    # a shell with $1 revenue and a $1M loss: net_margin = -1,000,000,
    # which does not fit NUMERIC(8,4)
    row = {"net_margin": Decimal(-1_000_000), "roe": Decimal(50_000)}
    out = _null_unstorable(row)
    assert out["net_margin"] is None
    assert out["roe"] is None


def test_normal_ratios_survive():
    row = {
        "net_margin": Decimal("0.3281"),
        "roe": Decimal("0.3183"),
        "pe_ttm": Decimal("32.61"),
        "debt_to_equity": Decimal("0.12"),
        "market_cap": Decimal("4311200000000"),
    }
    out = _null_unstorable(dict(row))
    assert out == row


def test_enormous_pe_from_a_near_zero_eps_becomes_null():
    # price / 0.0000001 blows past NUMERIC(12,4)
    row = {"pe_ttm": Decimal("999999999")}
    assert _null_unstorable(row)["pe_ttm"] is None


def test_null_values_are_left_alone():
    assert _null_unstorable({"roe": None})["roe"] is None
