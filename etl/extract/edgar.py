"""Extract: SEC EDGAR companyfacts bulk pull for the sample ticker universe.

Resolves ticker -> CIK via the public ticker map, upserts the company
dimension, then lands each company's raw companyfacts JSON to bronze
(local filesystem for MVP; swap for S3/MinIO later without touching
silver/gold). No ratio math and no financial_facts writes here -- that's
silver's job.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
from sqlalchemy.dialects.postgresql import insert
from tenacity import retry, stop_after_attempt, wait_exponential

from etl.config import settings
from etl.db import get_session
from etl.models import Company
from etl.tickers import SAMPLE_TICKERS

TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

# SEC's fair-access policy asks for <=10 req/s with a descriptive User-Agent
REQUEST_DELAY_SECONDS = 0.15


def _client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": settings.sec_edgar_user_agent}, timeout=30.0)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
def _get(client: httpx.Client, url: str) -> httpx.Response:
    resp = client.get(url)
    resp.raise_for_status()
    return resp


def resolve_ciks(tickers: list[str]) -> dict[str, dict]:
    """ticker -> {'cik_str': int, 'title': str}"""
    with _client() as client:
        data = _get(client, TICKER_MAP_URL).json()

    by_ticker = {row["ticker"]: row for row in data.values()}
    wanted_keys = list(dict.fromkeys(t.upper() for t in tickers))  # de-dup, preserve order
    resolved = {t: by_ticker[t] for t in wanted_keys if t in by_ticker}
    missing = set(wanted_keys) - resolved.keys()
    if missing:
        raise ValueError(f"tickers not found in EDGAR ticker map: {sorted(missing)}")
    return resolved


def upsert_companies(resolved: dict[str, dict]) -> dict[str, int]:
    """Insert/update companies rows for resolved tickers, return ticker -> security_id."""
    ticker_to_id: dict[str, int] = {}
    with get_session() as session:
        for ticker, row in resolved.items():
            stmt = (
                insert(Company)
                .values(cik=row["cik_str"], ticker=ticker, name=row["title"])
                .on_conflict_do_update(
                    index_elements=["cik"],
                    set_={"ticker": ticker, "name": row["title"]},
                )
                .returning(Company.security_id)
            )
            ticker_to_id[ticker] = session.execute(stmt).scalar_one()
    return ticker_to_id


def land_bronze(ticker: str, payload: dict) -> Path:
    bronze_dir = Path(settings.bronze_path) / "companyfacts"
    bronze_dir.mkdir(parents=True, exist_ok=True)
    path = bronze_dir / f"{ticker}.json"
    path.write_text(json.dumps(payload))
    return path


def extract_sample(tickers: list[str] | None = None) -> dict[str, int]:
    """Full extract step: resolve CIKs, upsert companies, land bronze JSON.

    Returns ticker -> security_id for downstream silver/gold steps.
    """
    tickers = tickers or SAMPLE_TICKERS
    resolved = resolve_ciks(tickers)
    ticker_to_id = upsert_companies(resolved)

    with _client() as client:
        for ticker, row in resolved.items():
            payload = _get(client, COMPANYFACTS_URL.format(cik=row["cik_str"])).json()
            land_bronze(ticker, payload)
            time.sleep(REQUEST_DELAY_SECONDS)

    return ticker_to_id


if __name__ == "__main__":
    ids = extract_sample()
    print(f"extracted {len(ids)} companies to bronze: {sorted(ids)}")
