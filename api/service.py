"""Read services: run screens and company drill-downs, cache-aside via Redis.

A screen is a single indexed SELECT over screener_metrics (invariant #1/#2) --
no joins, no external calls, no per-row math. Results are cached under a
version-scoped key so the daily GOLD refresh invalidates the whole namespace
at once (see api/cache.py).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from decimal import Decimal

from sqlalchemy import case, func, or_, select

from api import cache
from api.compiler import build_screen_query, encode_cursor
from api.models import CompanyResponse, PriceBar, ScreenRequest, ScreenResponse, SearchHit
from api.schema import daily_prices, fundamentals_periodic, screener_metrics
from api.db import engine

HISTORY_LIMIT = 12  # periods returned by the company drill-down
PRICE_BARS_LIMIT = 400  # cap on OHLCV bars per /prices request
SEARCH_LIMIT = 50


def _jsonable(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    return value


def _row_to_dict(row) -> dict:
    return {k: _jsonable(v) for k, v in dict(row).items()}


def _digest(req: ScreenRequest) -> str:
    canonical = json.dumps(req.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


async def run_screen(req: ScreenRequest) -> ScreenResponse:
    version = await cache.current_version()
    key = cache.make_key(version, _digest(req))

    hit = await cache.get_cached(key)
    if hit is not None:
        payload = json.loads(hit)
        return ScreenResponse(**payload, cached=True)

    stmt = build_screen_query(req.filter, req.limit, req.cursor)
    async with engine.connect() as conn:
        rows = (await conn.execute(stmt)).mappings().all()

    has_next = len(rows) > req.limit
    rows = rows[: req.limit]
    results = [_row_to_dict(r) for r in rows]

    next_cursor = None
    if has_next and rows:
        last = rows[-1]
        next_cursor = encode_cursor(last["market_cap"], last["security_id"])

    payload = {"results": results, "count": len(results), "next_cursor": next_cursor}
    await cache.set_cached(key, json.dumps(payload))
    return ScreenResponse(**payload, cached=False)


async def get_company(security_id: int) -> CompanyResponse | None:
    version = await cache.current_version()
    key = cache.make_key(version, f"company:{security_id}")

    hit = await cache.get_cached(key)
    if hit is not None:
        payload = json.loads(hit)
        return CompanyResponse(**payload, cached=True)

    metrics_stmt = select(screener_metrics).where(screener_metrics.c.security_id == security_id)
    history_stmt = (
        select(fundamentals_periodic)
        .where(fundamentals_periodic.c.security_id == security_id)
        .order_by(fundamentals_periodic.c.period_end.desc())
        .limit(HISTORY_LIMIT)
    )
    async with engine.connect() as conn:
        metrics_row = (await conn.execute(metrics_stmt)).mappings().first()
        if metrics_row is None:
            return None
        history_rows = (await conn.execute(history_stmt)).mappings().all()

    payload = {
        "company": _row_to_dict(metrics_row),
        "history": [_row_to_dict(r) for r in history_rows],
    }
    await cache.set_cached(key, json.dumps(payload))
    return CompanyResponse(**payload, cached=False)


def escape_like(text: str) -> str:
    """Neutralise LIKE wildcards in user input.

    Without this, a query of `%` matches every company and `_` matches any
    single character — the search silently stops meaning what the user typed.
    Values are still bound parameters (never interpolated), so this is about
    correctness rather than injection.
    """
    return text.replace("\\", r"\\").replace("%", r"\%").replace("_", r"\_")


async def search_companies(query: str, limit: int = 10) -> list[SearchHit]:
    """Find companies by ticker or name.

    Searches `screener_metrics` rather than `companies`, for two reasons: it is
    the screener's serving table (invariant #2), and a company absent from it has
    no metrics — so returning it would hand the user a result that 404s on the
    company page.

    Ranked so the obvious answer comes first: an exact ticker beats a ticker
    prefix, which beats a name prefix, which beats a match anywhere; ties break on
    market cap. So "AAPL" finds Apple, and "micro" surfaces Microsoft rather than
    a microcap.

    Not cached: search terms are unbounded, so caching them would let anyone fill
    Redis with junk keys. It's a scan of ~6k RAM-resident rows (§3.2) — microseconds.
    """
    raw = query.strip()
    if not raw:
        return []

    escaped = escape_like(raw)
    contains = f"%{escaped}%"
    starts = f"{escaped}%"
    sm = screener_metrics

    rank = case(
        (func.upper(sm.c.ticker) == raw.upper(), 0),
        (sm.c.ticker.ilike(starts, escape="\\"), 1),
        (sm.c.name.ilike(starts, escape="\\"), 2),
        else_=3,
    )

    stmt = (
        select(
            sm.c.security_id,
            sm.c.ticker,
            sm.c.name,
            sm.c.sector,
            sm.c.exchange,
            sm.c.price,
            sm.c.market_cap,
        )
        .where(
            or_(
                sm.c.ticker.ilike(contains, escape="\\"),
                sm.c.name.ilike(contains, escape="\\"),
            )
        )
        .order_by(rank, func.coalesce(sm.c.market_cap, -1).desc(), sm.c.ticker)
        .limit(min(limit, SEARCH_LIMIT))
    )

    async with engine.connect() as conn:
        rows = (await conn.execute(stmt)).mappings().all()
    return [SearchHit(**_row_to_dict(row)) for row in rows]


async def get_prices(security_id: int, days: int) -> list[PriceBar]:
    """OHLCV bars for the chart, oldest -> newest (hypertable read)."""
    days = max(1, min(days, PRICE_BARS_LIMIT))
    version = await cache.current_version()
    key = cache.make_key(version, f"prices:{security_id}:{days}")

    hit = await cache.get_cached(key)
    if hit is not None:
        return [PriceBar(**bar) for bar in json.loads(hit)]

    stmt = (
        select(
            daily_prices.c.dt,
            daily_prices.c.open,
            daily_prices.c.high,
            daily_prices.c.low,
            daily_prices.c.close,
            daily_prices.c.volume,
        )
        .where(daily_prices.c.security_id == security_id)
        .order_by(daily_prices.c.dt.desc())
        .limit(days)
    )
    async with engine.connect() as conn:
        rows = (await conn.execute(stmt)).mappings().all()

    bars = [_row_to_dict(r) for r in reversed(rows)]  # chart wants oldest first
    await cache.set_cached(key, json.dumps(bars))
    return [PriceBar(**bar) for bar in bars]
