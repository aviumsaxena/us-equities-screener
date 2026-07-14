"""The ticker universe.

`SAMPLE_TICKERS` is the 20-name smoke-test set (`python -m etl --sample`).
`full_universe()` is the real thing: every ticker SEC publishes a CIK for.

One subtlety that shapes the `companies` table: **several tickers can share one
CIK** (GOOG/GOOGL, BRK-A/BRK-B are one filer each), and SEC files fundamentals
per *filer*, not per share class. `companies.cik` is UNIQUE, so we keep one row
per CIK and pick a primary ticker deterministically -- otherwise the same
company would be upserted twice and flip-flop between class tickers on every
run.
"""
from __future__ import annotations

import httpx

from etl.config import settings

TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"

SAMPLE_TICKERS = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA",
    "META", "TSLA", "BRK-B", "JPM", "JNJ",
    "V", "WMT", "PG", "MA", "HD",
    "DIS", "KO", "PEP", "XOM", "CVX",
]


def fetch_ticker_map() -> list[dict]:
    """SEC's ticker -> CIK map: [{'cik_str': int, 'ticker': str, 'title': str}]."""
    with httpx.Client(headers={"User-Agent": settings.sec_edgar_user_agent}, timeout=60.0) as client:
        resp = client.get(TICKER_MAP_URL)
        resp.raise_for_status()
        return list(resp.json().values())


def primary_by_cik(entries: list[dict]) -> dict[int, dict]:
    """cik -> the single entry we treat as that filer's primary listing.

    Keeps SEC's **first** listing for each CIK. That is not arbitrary: SEC orders
    a filer's share classes with the primary/most-liquid one first — GOOGL before
    GOOG, BRK-B before BRK-A, FOXA before FOX — which is exactly the class a
    screener should show. (Sorting by ticker length or alphabetically would pick
    GOOG and BRK-A instead: the wrong class, and for BRK a ~1,500x different
    share price.)

    ~960 of the ~7,600 filers list more than one class. `companies.cik` is UNIQUE
    so we keep one row per *filer*, which means only the primary class is
    screenable and a multi-class issuer's `market_cap` (this class's price ×
    the consolidated share count) is an approximation. Representing every class
    would mean one row per *security* rather than per filer — see ARCHITECTURE §6.
    """
    best: dict[int, dict] = {}
    for entry in entries:
        best.setdefault(entry["cik_str"], entry)
    return best


def full_universe() -> list[dict]:
    """Every filer with a ticker, one entry per CIK."""
    return list(primary_by_cik(fetch_ticker_map()).values())
