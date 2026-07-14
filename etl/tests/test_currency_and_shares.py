"""Currency and share-count integrity — the two bugs that produced absurd valuations.

Both were only visible once the universe was the real ~7.6k filers, and both
produced *plausible-looking* rows rather than crashes, which is what makes them
dangerous: a $29 quadrillion market cap is obvious, but a P/E silently computed
from yen is not.
"""
from __future__ import annotations

from decimal import Decimal

from etl.gold.metrics import _validated_shares
from etl.silver.transform import _entries_for_concept, expected_unit


# --- currency: read only the concept's own unit -------------------------------

def test_expected_units():
    assert expected_unit("revenue") == "USD"
    assert expected_unit("net_income") == "USD"
    assert expected_unit("eps_diluted") == "USD/shares"
    assert expected_unit("shares_diluted") == "shares"


def test_foreign_currency_facts_are_not_read_as_usd():
    """Nomura files Revenues in both JPY and USD. Reading every unit made
    ¥4.76 trillion look like $4.76 trillion."""
    facts = {
        "us-gaap": {
            "Revenues": {
                "units": {
                    "JPY": [{"start": "2025-04-01", "end": "2026-03-31", "val": 4_758_486_000_000}],
                    "USD": [{"start": "2011-07-01", "end": "2011-09-30", "val": 4_905_000_000}],
                }
            }
        }
    }
    entries = _entries_for_concept(facts, ["Revenues"], "revenue")
    assert [e["val"] for e in entries] == [4_905_000_000]  # only the USD figure


def test_eps_in_foreign_currency_is_dropped():
    facts = {
        "us-gaap": {
            "EarningsPerShareDiluted": {
                "units": {
                    "JPY/shares": [{"end": "2026-03-31", "val": 118.99}],
                    "USD/shares": [{"end": "2011-09-30", "val": -0.16}],
                }
            }
        }
    }
    entries = _entries_for_concept(facts, ["EarningsPerShareDiluted"], "eps_diluted")
    assert [e["val"] for e in entries] == [-0.16]


def test_usd_only_filer_is_unaffected():
    facts = {"us-gaap": {"Revenues": {"units": {"USD": [{"end": "2025-12-31", "val": 100}]}}}}
    assert len(_entries_for_concept(facts, ["Revenues"], "revenue")) == 1


# --- share count: cross-check against the filer's own EPS and net income -------

def test_share_count_contradicting_eps_and_net_income_is_rejected():
    """Bicara filed 54,676,896,000 diluted shares for FY2025; its own EPS
    (-2.52) and net income imply ~54.7M. 1,000x out -> refuse to value it."""
    net_income = Decimal("-137785378")  # ~ -2.52 * 54.7M
    eps = Decimal("-2.52")
    assert _validated_shares(Decimal("54676896000"), net_income, eps) is None


def test_consistent_share_count_is_kept():
    net_income = Decimal("-137785378")
    eps = Decimal("-2.52")
    shares = Decimal("54676896")
    assert _validated_shares(shares, net_income, eps) == shares


def test_shares_kept_when_there_is_nothing_to_check_against():
    shares = Decimal("1000000")
    assert _validated_shares(shares, None, None) == shares
    assert _validated_shares(shares, Decimal(100), Decimal(0)) == shares


def test_absurd_share_count_rejected_even_with_nothing_to_cross_check():
    """Nomura filed 3,041,190,068,000,000 shares and has no recent USD earnings,
    so the cross-check had nothing to compare against and let it through --
    valuing the company at $29 quadrillion. The absolute ceiling must catch it
    on its own."""
    assert _validated_shares(Decimal("3041190068000000"), None, None) is None


def test_large_but_real_share_count_survives():
    # big banks / penny stocks legitimately reach the billions
    shares = Decimal("8000000000")
    assert _validated_shares(shares, None, None) == shares


def test_missing_or_nonpositive_shares_give_none():
    assert _validated_shares(None, Decimal(1), Decimal(1)) is None
    assert _validated_shares(Decimal(0), Decimal(1), Decimal(1)) is None
