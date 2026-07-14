"""Extract: company reference data (sector / industry / exchange) from SEC submissions.

The `submissions` endpoint carries each filer's SEC-assigned SIC code, its
description, and the exchanges it lists on -- free, no API key, and available
for every one of the 8k+ filers. `industry` is SEC's own SIC description;
`sector` is derived from the SIC code (see etl/sic.py for why SIC and not
licensed GICS).

Updates the `companies` dimension in place. GOLD denormalizes these columns
into screener_metrics, so re-run gold after this to make sector screens work.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Optional

import httpx
from sqlalchemy import select, update
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from etl.config import settings
from etl.db import get_session
from etl.models import Company
from etl.sic import sic_to_sector

log = logging.getLogger("etl.reference")

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
# SEC fair-access policy: stay well under 10 req/s
REQUEST_DELAY_SECONDS = 0.15


def _client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": settings.sec_edgar_user_agent}, timeout=30.0)


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return isinstance(exc, httpx.TransportError)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception(_is_retryable),
    reraise=True,
)
def _get(client: httpx.Client, cik: int) -> dict:
    resp = client.get(SUBMISSIONS_URL.format(cik=cik))
    resp.raise_for_status()
    return resp.json()


def land_bronze(ticker: str, payload: dict) -> Path:
    bronze_dir = Path(settings.bronze_path) / "submissions"
    bronze_dir.mkdir(parents=True, exist_ok=True)
    path = bronze_dir / f"{ticker}.json"
    path.write_text(json.dumps(payload))
    return path


def parse_reference(payload: dict) -> dict:
    """SEC submissions JSON -> the companies columns we maintain."""
    sic = payload.get("sic")
    exchanges = payload.get("exchanges") or []
    return {
        "sector": sic_to_sector(sic),
        "industry": payload.get("sicDescription") or None,
        "exchange": exchanges[0] if exchanges else None,
    }


def extract_reference(ticker_to_id: Optional[dict[str, int]] = None) -> int:
    """Refresh sector/industry/exchange on `companies`. Returns rows updated."""
    with get_session() as session:
        rows = session.execute(
            select(Company.security_id, Company.ticker, Company.cik).where(Company.cik.isnot(None))
        ).all()
    if ticker_to_id is not None:
        wanted = set(ticker_to_id)
        rows = [r for r in rows if r.ticker in wanted]

    updated = 0
    unmapped: list[str] = []
    with _client() as client:
        for security_id, ticker, cik in rows:
            try:
                payload = _get(client, cik)
            except httpx.HTTPError as exc:
                log.warning("skipping %s: %s", ticker, exc)
                continue
            land_bronze(ticker, payload)
            values = parse_reference(payload)
            if values["sector"] is None:
                unmapped.append(f"{ticker}(sic={payload.get('sic')})")
            with get_session() as session:
                session.execute(
                    update(Company).where(Company.security_id == security_id).values(**values)
                )
            updated += 1
            time.sleep(REQUEST_DELAY_SECONDS)

    if unmapped:
        log.warning("no sector mapped for %d ticker(s): %s", len(unmapped), unmapped)
    return updated


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    n = extract_reference()
    print(f"updated reference data for {n} companies")
