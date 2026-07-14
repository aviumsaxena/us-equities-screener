"""Data-quality guards in silver, built from filer typos found in real EDGAR data.

`financial_facts` is range-partitioned on fiscal_year, so a bogus year is not a
cosmetic problem: the INSERT hard-fails and takes the whole run down. These are
the two shapes that actually occurred across the ~7.6k-filer universe.
"""
from __future__ import annotations

import datetime as dt

from etl.silver.transform import _group_by_period, _is_impossible


def test_period_ending_after_its_filing_is_rejected():
    # NAII: a 10-Q filed 2023-05-15 reporting a period ending 2031-09-25
    assert _is_impossible(
        {"start": "2022-10-24", "end": "2031-09-25", "val": 32_699_000, "filed": "2023-05-15"}
    )


def test_instant_dated_after_its_filing_is_rejected():
    # QPRC: equity "as of" 2029-06-30, in a 10-Q filed 2020-08-14. This is the
    # one the duration filter cannot catch -- an instant has no duration.
    assert _is_impossible({"end": "2029-06-30", "val": -5_579_605, "filed": "2020-08-14"})


def test_normal_fact_is_kept():
    assert not _is_impossible(
        {"start": "2026-01-01", "end": "2026-03-31", "val": 1_000, "filed": "2026-04-30"}
    )


def test_period_ending_on_the_filing_date_is_kept():
    # end == filed is legitimate, only end > filed is impossible
    assert not _is_impossible({"end": "2026-03-31", "val": 1_000, "filed": "2026-03-31"})


def test_unfiled_fact_falls_back_to_today():
    future = (dt.date.today() + dt.timedelta(days=365)).isoformat()
    assert _is_impossible({"end": future, "val": 1_000})
    assert not _is_impossible({"end": "2020-12-31", "val": 1_000})


def test_group_by_period_drops_impossible_instants():
    entries = [
        {"end": "2020-06-30", "val": 100, "filed": "2020-08-14"},   # fine
        {"end": "2029-06-30", "val": -5_579_605, "filed": "2020-08-14"},  # typo
    ]
    grouped = _group_by_period(entries)
    assert set(grouped) == {"2020-06-30"}
