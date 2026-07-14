"""Extract: the `companies` dimension, from SEC's ticker -> CIK map.

The *facts* no longer come through here. At the real universe (~7.6k filers)
per-company API calls are the wrong shape -- `companyfacts` is 1-20 MB each --
so silver streams them out of the bulk archive instead (etl/bulk.py). This
module only resolves and upserts the company dimension, which is what everything
downstream keys off.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from etl.db import get_session
from etl.models import Company
from etl.tickers import fetch_ticker_map, full_universe, primary_by_cik

log = logging.getLogger("etl.edgar")

BATCH = 1000


def resolve_universe(tickers: list[str] | None = None) -> list[dict]:
    """Universe entries ({'cik_str', 'ticker', 'title'}), one per CIK.

    With `tickers`, restrict to those symbols (the sample run); without, take
    every filer SEC publishes a ticker for.
    """
    if tickers is None:
        return full_universe()

    wanted = {t.upper() for t in tickers}
    entries = [e for e in fetch_ticker_map() if e["ticker"].upper() in wanted]
    missing = wanted - {e["ticker"].upper() for e in entries}
    if missing:
        raise ValueError(f"tickers not found in EDGAR ticker map: {sorted(missing)}")
    return list(primary_by_cik(entries).values())


def upsert_companies(entries: list[dict]) -> dict[int, int]:
    """Insert/update the company dimension. Returns cik -> security_id.

    Batched: the full universe is thousands of rows, and one statement per
    company would mean thousands of round trips.
    """
    with get_session() as session:
        for start in range(0, len(entries), BATCH):
            payload = [
                dict(cik=e["cik_str"], ticker=e["ticker"], name=e["title"])
                for e in entries[start : start + BATCH]
            ]
            stmt = insert(Company)
            stmt = stmt.on_conflict_do_update(
                index_elements=["cik"],
                set_={"ticker": stmt.excluded.ticker, "name": stmt.excluded.name},
            )
            session.execute(stmt, payload)

    ciks = {e["cik_str"] for e in entries}
    with get_session() as session:
        rows = session.execute(
            select(Company.cik, Company.security_id).where(Company.cik.in_(ciks))
        ).all()
    return {cik: security_id for cik, security_id in rows}


def extract_companies(tickers: list[str] | None = None) -> dict[int, int]:
    entries = resolve_universe(tickers)
    cik_to_id = upsert_companies(entries)
    log.info("companies: %d filers in the universe", len(cik_to_id))
    return cik_to_id


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ids = extract_companies()
    print(f"upserted {len(ids)} companies")
