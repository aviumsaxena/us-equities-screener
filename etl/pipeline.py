"""Orchestrates bronze -> silver -> gold for a ticker universe. Idempotent."""
from __future__ import annotations

import logging

from etl.cache import bump_screen_cache_version
from etl.extract.edgar import extract_sample
from etl.gold.metrics import run_gold
from etl.seed.concepts import seed as seed_concepts
from etl.silver.transform import transform_all
from etl.tickers import SAMPLE_TICKERS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("etl.pipeline")


def run(tickers: list[str] | None = None) -> None:
    tickers = tickers or SAMPLE_TICKERS

    seed_concepts()
    log.info("seeded financial_concepts")

    ticker_to_id = extract_sample(tickers)
    log.info("extracted %d companies to bronze", len(ticker_to_id))

    n_facts = transform_all(ticker_to_id)
    log.info("wrote %d financial_facts rows", n_facts)

    n_metrics = run_gold()
    log.info("wrote screener_metrics for %d companies", n_metrics)

    # GOLD is fresh -> invalidate the screener's cache namespace (best-effort)
    bump_screen_cache_version()


if __name__ == "__main__":
    run()
