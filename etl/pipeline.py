"""Orchestrates bronze -> silver -> gold. Idempotent and re-runnable.

Scale note: fundamentals and reference data come from SEC's bulk archives (one
download each, streamed per CIK), and prices from a grouped-daily endpoint (one
call per trading day, whole market). So the run cost is essentially flat in the
size of the universe -- 20 tickers and 7,600 tickers pull the same bytes.
"""
from __future__ import annotations

import logging

from etl.cache import bump_screen_cache_version
from etl.extract.edgar import extract_companies
from etl.extract.prices import extract_prices
from etl.extract.reference import extract_reference
from etl.gold.metrics import run_gold
from etl.seed.concepts import seed as seed_concepts
from etl.silver.transform import transform_all

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("etl.pipeline")

# The daily run only needs the last few sessions (the window absorbs weekends
# and any late-published day). Deep history is a one-off, run explicitly:
#   python -m etl.extract.prices --days 504
DAILY_PRICE_WINDOW = 5


def run(tickers: list[str] | None = None, price_days: int = DAILY_PRICE_WINDOW) -> None:
    """`tickers=None` runs the full SEC universe; pass a list for a sample run."""
    seed_concepts()
    log.info("seeded financial_concepts")

    cik_to_id = extract_companies(tickers)
    log.info("universe: %d companies", len(cik_to_id))

    n_ref = extract_reference(cik_to_id)
    log.info("refreshed reference data (sector/industry/exchange) for %d companies", n_ref)

    n_facts = transform_all(cik_to_id)
    log.info("wrote %d financial_facts rows", n_facts)

    n_prices = extract_prices(days=price_days)
    log.info("wrote %d daily_prices rows", n_prices)

    n_metrics = run_gold()
    log.info("wrote screener_metrics for %d companies", n_metrics)

    # GOLD is fresh -> invalidate the screener's cache namespace (best-effort)
    bump_screen_cache_version()


if __name__ == "__main__":
    run()
