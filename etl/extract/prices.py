"""Extract: EOD prices from Polygon's Grouped Daily (Daily Market Summary).

One request returns *every* US ticker's OHLCV bar for a single trading day, so
API cost scales with **days of history, not with the size of the universe**:
staying current is 1 call/day whether we track 20 tickers or 8,000. (The
previous vendor was per-ticker, so 8k tickers meant 8k calls/day -- a wall no
free tier survives. That shape, not the price, is why we moved.)

Two consequences worth knowing:

* **Bronze holds the whole market, not just our universe.** Each day's raw
  response is landed verbatim, then filtered to the tickers we track. Widening
  the universe later therefore costs **zero** API calls -- re-running replays
  the existing bronze files. This is the "replayable ETL" the object store was
  always for (ARCHITECTURE.md §1).
* **The backfill is slow but resumable.** The free tier allows 5 requests/min,
  so we sleep between calls; a date whose bronze file already exists is never
  re-fetched, so an interrupted run picks up exactly where it stopped.

The API key travels in an `Authorization: Bearer` header, never a query param,
so it cannot leak through a logged URL. httpx's INFO request logging is muted
below as defence in depth.
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
import logging
import time
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Iterator, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from etl.config import settings
from etl.db import get_session
from etl.models import Company, DailyPrice

log = logging.getLogger("etl.prices")

# Belt-and-braces: we use header auth so no URL carries the key, but a future
# vendor swap might not, and httpx logs full URLs at INFO.
logging.getLogger("httpx").setLevel(logging.WARNING)

GROUPED_URL = "https://api.polygon.io/v2/aggs/grouped/locale/us/market/stocks/{date}"
# free tier: 5 requests/minute -> keep calls >=12s apart (respecting the limit)
REQUEST_DELAY_SECONDS = 13.0
DEFAULT_BACKFILL_DAYS = 120
_UPSERT_COLUMNS = ("open", "high", "low", "close", "adj_close", "volume")
_QUANT = Decimal("0.0001")


class PriceVendorError(RuntimeError):
    """Vendor returned a non-OK payload (bad key, plan limit, unknown date)."""


def _client() -> httpx.Client:
    return httpx.Client(
        headers={"Authorization": f"Bearer {settings.polygon_api_key}"},
        timeout=60.0,  # a full-market day is a few MB
    )


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return isinstance(exc, httpx.TransportError)


@retry(
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=2, min=5, max=60),
    retry=retry_if_exception(_is_retryable),
    reraise=True,
)
def _get(client: httpx.Client, day: dt.date) -> dict:
    resp = client.get(GROUPED_URL.format(date=day.isoformat()), params={"adjusted": "true"})
    resp.raise_for_status()
    payload = resp.json()
    status = payload.get("status")
    # Polygon signals plan/key problems in the body; a market holiday is a
    # legitimate OK-with-no-results, not an error
    if status not in ("OK", "DELAYED"):
        raise PriceVendorError(payload.get("error") or f"unexpected status {status!r}")
    return payload


def _num(value) -> Optional[Decimal]:
    if value is None:
        return None
    return Decimal(str(value)).quantize(_QUANT, rounding=ROUND_HALF_UP)


def vendor_symbols(ticker: str) -> list[str]:
    """Every symbol this vendor might use for one of our tickers.

    Our tickers come from SEC, which writes share classes with a hyphen
    (`BRK-B`); Polygon writes them with a dot (`BRK.B`). Without this the
    lookup misses and the ticker silently gets *no prices at all* -- harmless-
    looking at 20 tickers, but it would quietly drop every multi-class share
    (BRK.A/B, BF.A/B, ...) across an 8k universe.
    """
    symbols = [ticker]
    if "-" in ticker:
        symbols.append(ticker.replace("-", "."))
    return symbols


def build_symbol_index(ticker_to_id: dict[str, int]) -> dict[str, int]:
    """our ticker -> security_id  ==>  every vendor symbol -> security_id"""
    index: dict[str, int] = {}
    for ticker, security_id in ticker_to_id.items():
        for symbol in vendor_symbols(ticker):
            index[symbol] = security_id
    return index


def parse_grouped(payload: dict, symbol_index: dict[str, int]) -> list[dict]:
    """Grouped-daily payload -> daily_prices rows for the tickers we track.

    `symbol_index` maps *vendor* symbols to security_ids (build it with
    build_symbol_index). The response covers the whole market (~12k tickers);
    everything outside our universe is dropped here rather than at fetch time,
    so the bronze file stays a complete market snapshot we can replay against a
    wider universe later, for free.

    Note `adjusted=true`: OHLC is split-adjusted, so `close` and `adj_close`
    hold the same value. The latest bar is unaffected by adjustment, which is
    what market cap / P-E need; charts want the adjusted series anyway.
    """
    rows = []
    for bar in payload.get("results") or []:
        security_id = symbol_index.get(bar.get("T"))
        if security_id is None:
            continue
        close = _num(bar.get("c"))
        ts = bar.get("t")
        if close is None or ts is None:
            continue
        volume = bar.get("v")
        rows.append(
            dict(
                security_id=security_id,
                # 't' is the ms epoch of the bar's start, in US market time
                dt=dt.datetime.fromtimestamp(ts / 1000, tz=dt.timezone.utc).date(),
                open=_num(bar.get("o")),
                high=_num(bar.get("h")),
                low=_num(bar.get("l")),
                close=close,
                adj_close=close,
                volume=int(volume) if volume is not None else None,
            )
        )
    return rows


def _bronze_path(day: dt.date) -> Path:
    # gzipped: each snapshot is the whole market (~12k tickers, a few MB), and
    # a 2-year backfill keeps ~500 of them
    return Path(settings.bronze_path) / "prices" / f"{day.isoformat()}.json.gz"


def land_bronze(day: dt.date, payload: dict) -> Path:
    path = _bronze_path(day)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(json.dumps(payload).encode()))
    return path


def read_bronze(day: dt.date) -> dict:
    return json.loads(gzip.decompress(_bronze_path(day).read_bytes()))


def load_prices(rows: list[dict]) -> int:
    if not rows:
        return 0
    stmt = insert(DailyPrice)
    stmt = stmt.on_conflict_do_update(
        index_elements=["security_id", "dt"],
        set_={col: getattr(stmt.excluded, col) for col in _UPSERT_COLUMNS},
    )
    with get_session() as session:
        session.execute(stmt, rows)
    return len(rows)


def trading_days(end: dt.date, count: int) -> Iterator[dt.date]:
    """Weekdays, newest first. Market holidays simply come back empty from the
    vendor, so there's no holiday calendar to maintain."""
    day = end
    yielded = 0
    while yielded < count:
        if day.weekday() < 5:  # Mon-Fri
            yield day
            yielded += 1
        day -= dt.timedelta(days=1)


def _ticker_map() -> dict[str, int]:
    with get_session() as session:
        return dict(session.execute(select(Company.ticker, Company.security_id)).all())


def extract_prices(
    ticker_to_id: Optional[dict[str, int]] = None,
    days: int = DEFAULT_BACKFILL_DAYS,
    end: Optional[dt.date] = None,
) -> int:
    """Backfill `days` trading days of prices. Returns rows written.

    Idempotent and resumable: a day already in bronze is replayed from disk
    instead of re-fetched, so re-runs (and a widened ticker universe) cost no
    API calls.
    """
    if not settings.polygon_api_key:
        raise RuntimeError(
            "POLYGON_API_KEY is not set. Get a free key at https://polygon.io "
            "and add it to your .env, then re-run."
        )

    ticker_to_id = ticker_to_id or _ticker_map()
    symbol_index = build_symbol_index(ticker_to_id)
    # EOD for the current session isn't published until after the close; start
    # from the previous day so we never burn a call on an empty "today"
    end = end or (dt.date.today() - dt.timedelta(days=1))

    total = 0
    fetched = 0
    replayed = 0
    with _client() as client:
        for day in trading_days(end, days):
            if _bronze_path(day).exists():
                payload = read_bronze(day)  # no API call: replayed from disk
                replayed += 1
            else:
                try:
                    payload = _get(client, day)
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code == 403:
                        # the plan's history horizon (2 years on the free tier).
                        # Everything older will 403 too, so stop rather than
                        # burn the rate limit walking further back.
                        log.info("reached the plan's history limit at %s; stopping", day)
                        break
                    log.warning("skipping %s: %s", day, exc)
                    time.sleep(REQUEST_DELAY_SECONDS)
                    continue
                except (httpx.HTTPError, PriceVendorError) as exc:
                    log.warning("skipping %s: %s", day, exc)
                    time.sleep(REQUEST_DELAY_SECONDS)
                    continue
                land_bronze(day, payload)
                fetched += 1
                time.sleep(REQUEST_DELAY_SECONDS)

            rows = parse_grouped(payload, symbol_index)
            total += load_prices(rows)
            if rows:
                log.info("%s: %d rows", day, len(rows))

    log.info(
        "prices: %d rows | %d day(s) fetched from vendor, %d replayed from bronze",
        total, fetched, replayed,
    )
    return total


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Backfill EOD prices from Polygon")
    parser.add_argument("--days", type=int, default=DEFAULT_BACKFILL_DAYS)
    args = parser.parse_args()

    n = extract_prices(days=args.days)
    print(f"wrote {n} daily_prices rows")
