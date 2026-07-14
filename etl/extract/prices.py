"""Extract: EOD daily prices from Alpha Vantage (TIME_SERIES_DAILY).

Alpha Vantage's free key is issued instantly with no email confirmation, which
is why it's the MVP price source. Everything price-specific is isolated here +
in the daily_prices load, so this one module can be swapped for another vendor
without touching silver/gold.

Free-tier limits are tight (25 req/day, 5/min), so we fetch each ticker once,
space calls out, and land raw JSON to bronze -- re-running gold reads the DB,
never re-fetches. Idempotent on (security_id, dt).

Two free-tier constraints worth knowing, both of which only bound *history*,
not the latest close that the price-derived gold metrics need:
- `outputsize=full` is premium, so we fetch `compact`: the last ~100 trading
  days, not the 10 years ARCHITECTURE.md targets. Deep history (charts,
  52-week-high screens, the continuous aggregates) needs a paid tier or a
  different vendor -- swap this module, nothing else.
- TIME_SERIES_DAILY is *unadjusted* (adjusted close is premium), so adj_close
  is stored equal to close; fine for current price/market cap, but historical
  split adjustments are not reflected.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import time
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Optional

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from etl.config import settings
from etl.db import get_session
from etl.models import Company, DailyPrice

log = logging.getLogger("etl.prices")

# The vendor supports no header auth, so the API key must ride in the query
# string -- and httpx logs full request URLs at INFO, which leaks it into any
# app that configures logging at INFO. Keep httpx quiet here, at the module
# that owns the secret, rather than relying on every caller's log config.
logging.getLogger("httpx").setLevel(logging.WARNING)

BASE_URL = "https://www.alphavantage.co/query"
TIME_SERIES_KEY = "Time Series (Daily)"
HISTORY_YEARS = 10
_UPSERT_COLUMNS = ("open", "high", "low", "close", "adj_close", "volume")
_QUANT = Decimal("0.0001")
# free tier allows 5 req/min -> keep calls >=12s apart (respecting the limit,
# not working around it)
REQUEST_DELAY_SECONDS = 13.0


class PriceVendorError(RuntimeError):
    """Vendor returned a 200 but no price series (rate limit / bad symbol)."""


def _cutoff() -> dt.date:
    today = dt.date.today()
    return today.replace(year=today.year - HISTORY_YEARS)


def _client() -> httpx.Client:
    return httpx.Client(timeout=30.0)


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return isinstance(exc, httpx.TransportError)


# reraise=True so the underlying httpx error (not tenacity's RetryError) escapes
# and the per-ticker handler can skip on it
@retry(
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    retry=retry_if_exception(_is_retryable),
    reraise=True,
)
def _get(client: httpx.Client, symbol: str) -> dict:
    # apikey must be a query param (vendor supports no header auth); the httpx
    # logger is muted at module import so the URL isn't logged with it
    resp = client.get(
        BASE_URL,
        params={
            "function": "TIME_SERIES_DAILY",
            "symbol": symbol,
            "outputsize": "compact",  # 'full' is premium; see module docstring
            "apikey": settings.alphavantage_api_key,
        },
    )
    resp.raise_for_status()
    data = resp.json()
    if TIME_SERIES_KEY not in data:
        # Alpha Vantage signals rate limits / bad symbols with a 200 + a
        # Note/Information/Error Message field rather than an HTTP error
        msg = data.get("Note") or data.get("Information") or data.get("Error Message") or str(data)[:200]
        raise PriceVendorError(msg)
    return data


def _num(value) -> Optional[Decimal]:
    if value is None:
        return None
    return Decimal(str(value)).quantize(_QUANT, rounding=ROUND_HALF_UP)


def parse_prices(payload: dict, cutoff: Optional[dt.date] = None) -> list[dict]:
    """Alpha Vantage TIME_SERIES_DAILY JSON -> OHLCV row dicts, keeping only
    the last HISTORY_YEARS of data."""
    cutoff = cutoff or _cutoff()
    series = payload.get(TIME_SERIES_KEY, {})
    rows = []
    for date_str, bar in series.items():
        day = dt.date.fromisoformat(date_str)
        if day < cutoff:
            continue
        close = _num(bar.get("4. close"))
        if close is None:
            continue
        volume = bar.get("5. volume")
        rows.append(
            dict(
                dt=day,
                open=_num(bar.get("1. open")),
                high=_num(bar.get("2. high")),
                low=_num(bar.get("3. low")),
                close=close,
                adj_close=close,  # unadjusted endpoint; see module docstring
                volume=int(volume) if volume is not None else None,
            )
        )
    return rows


def land_bronze(ticker: str, payload: dict) -> Path:
    bronze_dir = Path(settings.bronze_path) / "prices"
    bronze_dir.mkdir(parents=True, exist_ok=True)
    path = bronze_dir / f"{ticker}.json"
    path.write_text(json.dumps(payload))
    return path


def load_prices(security_id: int, rows: list[dict]) -> int:
    if not rows:
        return 0
    payload = [dict(security_id=security_id, **row) for row in rows]
    stmt = insert(DailyPrice)
    stmt = stmt.on_conflict_do_update(
        index_elements=["security_id", "dt"],
        set_={col: getattr(stmt.excluded, col) for col in _UPSERT_COLUMNS},
    )
    with get_session() as session:
        session.execute(stmt, payload)
    return len(payload)


def extract_prices(ticker_to_id: Optional[dict[str, int]] = None) -> int:
    """Fetch + land + load EOD prices for the given tickers (defaults to every
    company already in the DB). Returns total rows written to daily_prices."""
    if not settings.alphavantage_api_key:
        raise RuntimeError(
            "ALPHAVANTAGE_API_KEY is not set. Get a free key (issued instantly, "
            "no email confirmation) at https://www.alphavantage.co/support/#api-key "
            "and add it to your .env, then re-run."
        )

    if ticker_to_id is None:
        with get_session() as session:
            ticker_to_id = dict(session.execute(select(Company.ticker, Company.security_id)).all())

    total = 0
    skipped: list[str] = []
    with _client() as client:
        for ticker, security_id in ticker_to_id.items():
            try:
                payload = _get(client, ticker)
            except (httpx.HTTPError, PriceVendorError) as exc:
                # coverage gaps / rate limits happen; skip and keep going
                log.warning("skipping %s: %s", ticker, exc)
                skipped.append(ticker)
                time.sleep(REQUEST_DELAY_SECONDS)
                continue
            land_bronze(ticker, payload)
            total += load_prices(security_id, parse_prices(payload))
            time.sleep(REQUEST_DELAY_SECONDS)
    if skipped:
        log.warning("no price data for %d/%d tickers: %s", len(skipped), len(ticker_to_id), skipped)
    return total


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    n = extract_prices()
    print(f"wrote {n} daily_prices rows")
