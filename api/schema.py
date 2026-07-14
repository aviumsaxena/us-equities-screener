"""The screener's read contract: a lightweight Core view of the GOLD tables
plus the field whitelist that drives (and secures) the /screen compiler.

This module is the API's own copy of the `screener_metrics` /
`fundamentals_periodic` column contract -- the api/ module deliberately does
NOT import etl/models, so the two stay independently deployable (they share
only the DB schema, per ARCHITECTURE.md §4).

FIELDS is a *curated* subset: only columns a user may filter on, each mapped
to {column, kind, allowed ops}. Anything not in FIELDS is rejected by the
compiler -- this whitelist is the SQL-injection boundary (invariant #3).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet

from sqlalchemy import BigInteger, Boolean, Column, Date, MetaData, Numeric, Table, Text

metadata = MetaData()

screener_metrics = Table(
    "screener_metrics",
    metadata,
    Column("security_id", BigInteger, primary_key=True),
    Column("ticker", Text),
    Column("name", Text),
    Column("sector", Text),
    Column("industry", Text),
    Column("exchange", Text),
    Column("price", Numeric),
    Column("market_cap", Numeric),
    Column("pe_ttm", Numeric),
    Column("pb", Numeric),
    Column("ps_ttm", Numeric),
    Column("ev_ebitda", Numeric),
    Column("dividend_yield", Numeric),
    Column("gross_margin", Numeric),
    Column("operating_margin", Numeric),
    Column("net_margin", Numeric),
    Column("roe", Numeric),
    Column("roce", Numeric),
    Column("revenue_ttm", Numeric),
    Column("revenue_growth_yoy", Numeric),
    Column("eps_growth_yoy", Numeric),
    Column("revenue_cagr_3y", Numeric),
    Column("debt_to_equity", Numeric),
    Column("current_ratio", Numeric),
    Column("interest_coverage", Numeric),
    Column("rev_up_4q", Boolean),
    Column("profitable_5y", Boolean),
    Column("price_asof", Date),
    Column("fundamentals_asof", Date),
)

daily_prices = Table(
    "daily_prices",
    metadata,
    Column("security_id", BigInteger, primary_key=True),
    Column("dt", Date, primary_key=True),
    Column("open", Numeric),
    Column("high", Numeric),
    Column("low", Numeric),
    Column("close", Numeric),
    Column("adj_close", Numeric),
    Column("volume", BigInteger),
)

fundamentals_periodic = Table(
    "fundamentals_periodic",
    metadata,
    Column("security_id", BigInteger, primary_key=True),
    Column("period_end", Date, primary_key=True),
    Column("fiscal_year", BigInteger),
    Column("fiscal_period", Text),
    Column("revenue", Numeric),
    Column("net_income", Numeric),
    Column("eps_diluted", Numeric),
    Column("total_assets", Numeric),
    Column("total_equity", Numeric),
    Column("total_debt", Numeric),
    Column("operating_cf", Numeric),
    Column("free_cf", Numeric),
)

# allowed operator sets per column kind (ARCHITECTURE.md §3.4)
NUMERIC_OPS: FrozenSet[str] = frozenset({"=", "!=", "<", "<=", ">", ">=", "BETWEEN", "IN"})
TEXT_OPS: FrozenSet[str] = frozenset({"=", "!=", "IN"})
BOOL_OPS: FrozenSet[str] = frozenset({"=", "!="})


@dataclass(frozen=True)
class FieldSpec:
    column: Column
    kind: str  # 'numeric' | 'text' | 'boolean'
    ops: FrozenSet[str]


def _num(name: str) -> FieldSpec:
    return FieldSpec(screener_metrics.c[name], "numeric", NUMERIC_OPS)


def _txt(name: str) -> FieldSpec:
    return FieldSpec(screener_metrics.c[name], "text", TEXT_OPS)


def _bool(name: str) -> FieldSpec:
    return FieldSpec(screener_metrics.c[name], "boolean", BOOL_OPS)


# The whitelist. Identity columns like `name` are display-only (returned, not
# filterable); freshness/surrogate columns are intentionally excluded.
FIELDS: dict[str, FieldSpec] = {
    "ticker": _txt("ticker"),
    "sector": _txt("sector"),
    "industry": _txt("industry"),
    "exchange": _txt("exchange"),
    "price": _num("price"),
    "market_cap": _num("market_cap"),
    "pe_ttm": _num("pe_ttm"),
    "pb": _num("pb"),
    "ps_ttm": _num("ps_ttm"),
    "ev_ebitda": _num("ev_ebitda"),
    "dividend_yield": _num("dividend_yield"),
    "gross_margin": _num("gross_margin"),
    "operating_margin": _num("operating_margin"),
    "net_margin": _num("net_margin"),
    "roe": _num("roe"),
    "roce": _num("roce"),
    "revenue_ttm": _num("revenue_ttm"),
    "revenue_growth_yoy": _num("revenue_growth_yoy"),
    "eps_growth_yoy": _num("eps_growth_yoy"),
    "revenue_cagr_3y": _num("revenue_cagr_3y"),
    "debt_to_equity": _num("debt_to_equity"),
    "current_ratio": _num("current_ratio"),
    "interest_coverage": _num("interest_coverage"),
    "rev_up_4q": _bool("rev_up_4q"),
    "profitable_5y": _bool("profitable_5y"),
}

# fixed projection returned by /screen (display columns, join-free)
RESULT_COLUMNS = [
    screener_metrics.c.security_id,
    screener_metrics.c.ticker,
    screener_metrics.c.name,
    screener_metrics.c.sector,
    screener_metrics.c.industry,
    screener_metrics.c.exchange,
    screener_metrics.c.price,
    screener_metrics.c.market_cap,
    screener_metrics.c.pe_ttm,
    screener_metrics.c.pb,
    screener_metrics.c.ps_ttm,
    screener_metrics.c.roe,
    screener_metrics.c.net_margin,
    screener_metrics.c.revenue_growth_yoy,
    screener_metrics.c.revenue_cagr_3y,
    screener_metrics.c.debt_to_equity,
    screener_metrics.c.current_ratio,
    screener_metrics.c.rev_up_4q,
    screener_metrics.c.profitable_5y,
    screener_metrics.c.fundamentals_asof,
]
