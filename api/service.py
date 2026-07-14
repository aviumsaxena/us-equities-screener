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

from sqlalchemy import select

from api import cache
from api.compiler import build_screen_query, encode_cursor
from api.models import CompanyResponse, PriceBar, ScreenRequest, ScreenResponse
from api.schema import daily_prices, fundamentals_periodic, screener_metrics
from api.db import engine

HISTORY_LIMIT = 12  # periods returned by the company drill-down
PRICE_BARS_LIMIT = 400  # cap on OHLCV bars per /prices request


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
