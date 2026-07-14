"""Gold: financial_facts (+ daily_prices) -> fundamentals_periodic + screener_metrics.

Idempotent and re-runnable: recomputes every ratio from the latest version
of each financial_facts row and upserts the gold tables. Price-derived
columns (price, market_cap, pe_ttm, pb, ps_ttm) come from the latest
daily_prices close × the latest fundamentals; ev_ebitda and dividend_yield
stay NULL pending more source data (D&A + cash concepts / a dividends load).

MVP simplifications:
- "TTM"/"latest" uses the most recent annual (fp='FY') period rather than a
  true trailing-twelve-month roll-up of quarters.
- market_cap uses diluted weighted-average shares (shares_diluted) as the
  share-count proxy -- refine to point-in-time shares outstanding later.
Both land once the underlying data is reliably present for all tickers.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from etl.db import get_session
from etl.models import (
    Company,
    DailyPrice,
    FinancialConcept,
    FinancialFact,
    FundamentalsPeriodic,
    ScreenerMetrics,
)

PERIODIC_COLUMNS = (
    "fiscal_year", "fiscal_period", "revenue", "net_income", "eps_diluted",
    "total_assets", "total_equity", "total_debt", "operating_cf", "free_cf",
)
METRICS_COLUMNS = tuple(c.name for c in ScreenerMetrics.__table__.columns if c.name != "security_id")


def _latest_facts(session) -> list:
    """One row per (security_id, concept_key, fiscal_year, fiscal_period) at its latest version."""
    stmt = (
        select(
            FinancialFact.security_id,
            FinancialConcept.concept_key,
            FinancialFact.fiscal_year,
            FinancialFact.fiscal_period,
            FinancialFact.period_end,
            FinancialFact.value,
        )
        .join(FinancialConcept, FinancialConcept.concept_id == FinancialFact.concept_id)
        .order_by(
            FinancialFact.security_id,
            FinancialConcept.concept_key,
            FinancialFact.fiscal_year,
            FinancialFact.fiscal_period,
            FinancialFact.version.desc(),
        )
    )
    rows = session.execute(stmt).all()
    latest = {}
    for r in rows:
        key = (r.security_id, r.concept_key, r.fiscal_year, r.fiscal_period)
        latest.setdefault(key, r)  # version DESC order -> first hit is the latest version
    return list(latest.values())


def _latest_prices(session) -> dict:
    """security_id -> latest (close, dt) via DISTINCT ON (one indexed pass)."""
    stmt = (
        select(DailyPrice.security_id, DailyPrice.dt, DailyPrice.close)
        .distinct(DailyPrice.security_id)
        .order_by(DailyPrice.security_id, DailyPrice.dt.desc())
    )
    return {r.security_id: (r.close, r.dt) for r in session.execute(stmt)}


def _pivot(rows: list) -> dict:
    """security_id -> {period_end: {concept_key: Decimal, 'period_end', 'fiscal_year', 'fiscal_period'}}

    Keyed by the fact's real period_end rather than (fiscal_year,
    fiscal_period): different concepts can derive different type labels for
    the same real date (see silver/transform.py's _period_type docstring),
    so period_end is the only join key guaranteed not to split or collide
    across concepts. Once any concept calls a date 'FY', that wins.
    """
    out: dict = defaultdict(lambda: defaultdict(dict))
    for r in rows:
        bucket = out[r.security_id][r.period_end]
        bucket[r.concept_key] = r.value
        bucket["period_end"] = r.period_end
        bucket["fiscal_year"] = r.fiscal_year
        if bucket.get("fiscal_period") != "FY":
            bucket["fiscal_period"] = r.fiscal_period
    return out


def _sorted_periods(periods: dict, fp: Optional[str] = None) -> list:
    items = [
        (key, data) for key, data in periods.items()
        if data.get("period_end") is not None and (fp is None or data.get("fiscal_period") == fp)
    ]
    return sorted(items, key=lambda kv: kv[1]["period_end"], reverse=True)


def _safe_div(a, b) -> Optional[Decimal]:
    if a is None or b is None or b == 0:
        return None
    return a / b


def _cagr(latest: Optional[Decimal], earliest: Optional[Decimal], years: int) -> Optional[Decimal]:
    if latest is None or earliest is None or earliest <= 0 or latest <= 0 or years <= 0:
        return None
    growth = (float(latest) / float(earliest)) ** (1 / years) - 1
    return Decimal(str(round(growth, 6)))


def _build_fundamentals_periodic_rows(security_id: int, periods: dict) -> list[dict]:
    rows = []
    for period_end, data in periods.items():
        long_debt = data.get("long_term_debt") or Decimal(0)
        short_debt = data.get("short_term_debt") or Decimal(0)
        total_debt = (long_debt + short_debt) or None
        operating_cf = data.get("operating_cf")
        capex = data.get("capex")
        free_cf = (operating_cf - capex) if operating_cf is not None and capex is not None else None

        rows.append(dict(
            security_id=security_id,
            period_end=period_end,
            fiscal_year=data.get("fiscal_year"),
            fiscal_period=data.get("fiscal_period"),
            revenue=data.get("revenue"),
            net_income=data.get("net_income"),
            eps_diluted=data.get("eps_diluted"),
            total_assets=data.get("total_assets"),
            total_equity=data.get("total_equity"),
            total_debt=total_debt,
            operating_cf=operating_cf,
            free_cf=free_cf,
        ))
    return rows


def _history_flags(periods: dict) -> dict:
    quarters = _sorted_periods(periods, fp=None)
    quarters = [(k, d) for k, d in quarters if d.get("fiscal_period") != "FY"][:4]
    rev_up_4q = None
    if len(quarters) == 4:
        revs = [d.get("revenue") for _k, d in reversed(quarters)]  # oldest -> newest
        if all(r is not None for r in revs):
            rev_up_4q = all(revs[i] < revs[i + 1] for i in range(len(revs) - 1))

    fys = _sorted_periods(periods, fp="FY")[:5]
    profitable_5y = None
    if len(fys) == 5:
        nis = [d.get("net_income") for _k, d in fys]
        if all(ni is not None for ni in nis):
            profitable_5y = all(ni > 0 for ni in nis)

    return {"rev_up_4q": rev_up_4q, "profitable_5y": profitable_5y}


def _build_screener_metrics_row(
    security_id: int, periods: dict, company: Company, price_row: Optional[tuple] = None
) -> dict:
    row = {col: None for col in METRICS_COLUMNS}
    row["security_id"] = security_id
    row["ticker"] = company.ticker
    row["name"] = company.name
    row["sector"] = company.sector
    row["industry"] = company.industry
    row["exchange"] = company.exchange

    fys = _sorted_periods(periods, fp="FY")
    if not fys:
        _apply_price_metrics(row, price_row, revenue=None, total_equity=None, eps=None, shares=None)
        return row

    (_end0, latest), *rest = fys
    prev = rest[0][1] if len(rest) >= 1 else None
    y3 = rest[2][1] if len(rest) >= 3 else None

    revenue = latest.get("revenue")
    gross_profit = latest.get("gross_profit")
    if gross_profit is None and latest.get("cost_of_revenue") is not None and revenue is not None:
        gross_profit = revenue - latest["cost_of_revenue"]
    operating_income = latest.get("operating_income")
    net_income = latest.get("net_income")
    total_equity = latest.get("total_equity")
    total_assets = latest.get("total_assets")
    current_assets = latest.get("current_assets")
    current_liabilities = latest.get("current_liabilities")
    long_debt = latest.get("long_term_debt") or Decimal(0)
    short_debt = latest.get("short_term_debt") or Decimal(0)
    total_debt = (long_debt + short_debt) or None
    interest_expense = latest.get("interest_expense")

    row.update(
        gross_margin=_safe_div(gross_profit, revenue),
        operating_margin=_safe_div(operating_income, revenue),
        net_margin=_safe_div(net_income, revenue),
        roe=_safe_div(net_income, total_equity),
        roce=_safe_div(
            operating_income,
            (total_assets - current_liabilities) if total_assets is not None and current_liabilities is not None else None,
        ),
        revenue_ttm=revenue,
        revenue_growth_yoy=_safe_div(revenue - prev["revenue"], prev["revenue"]) if prev and prev.get("revenue") is not None and revenue is not None else None,
        eps_growth_yoy=_safe_div(latest.get("eps_diluted") - prev["eps_diluted"], prev["eps_diluted"]) if prev and prev.get("eps_diluted") is not None and latest.get("eps_diluted") is not None else None,
        revenue_cagr_3y=_cagr(revenue, y3.get("revenue") if y3 else None, 3),
        debt_to_equity=_safe_div(total_debt, total_equity),
        current_ratio=_safe_div(current_assets, current_liabilities),
        interest_coverage=_safe_div(operating_income, abs(interest_expense) if interest_expense else None),
        fundamentals_asof=latest["period_end"],
    )
    row.update(_history_flags(periods))
    _apply_price_metrics(
        row,
        price_row,
        revenue=revenue,
        total_equity=total_equity,
        eps=latest.get("eps_diluted"),
        shares=latest.get("shares_diluted"),
    )
    return row


def _apply_price_metrics(row, price_row, *, revenue, total_equity, eps, shares) -> None:
    """Fill price-derived columns from the latest close + latest fundamentals.
    Left NULL when price or the needed fundamental is missing."""
    if price_row is None:
        return
    price, price_asof = price_row
    row["price"] = price
    row["price_asof"] = price_asof
    if price is None:
        return

    market_cap = price * shares if shares is not None else None
    row["market_cap"] = market_cap
    # PE only where earnings are positive; a negative/zero P/E isn't meaningful
    row["pe_ttm"] = _safe_div(price, eps) if (eps is not None and eps > 0) else None
    row["ps_ttm"] = _safe_div(market_cap, revenue)
    row["pb"] = _safe_div(market_cap, total_equity)


def run_gold() -> int:
    with get_session() as session:
        rows = _latest_facts(session)
        companies = {c.security_id: c for c in session.execute(select(Company)).scalars()}
        prices = _latest_prices(session)

    pivoted = _pivot(rows)

    periodic_rows: list[dict] = []
    metrics_rows: list[dict] = []
    for security_id, periods in pivoted.items():
        company = companies.get(security_id)
        if company is None:
            continue
        periodic_rows.extend(_build_fundamentals_periodic_rows(security_id, periods))
        metrics_rows.append(
            _build_screener_metrics_row(security_id, periods, company, prices.get(security_id))
        )

    with get_session() as session:
        if periodic_rows:
            stmt = insert(FundamentalsPeriodic)
            stmt = stmt.on_conflict_do_update(
                index_elements=["security_id", "period_end"],
                set_={col: getattr(stmt.excluded, col) for col in PERIODIC_COLUMNS},
            )
            session.execute(stmt, periodic_rows)

        if metrics_rows:
            stmt = insert(ScreenerMetrics)
            stmt = stmt.on_conflict_do_update(
                index_elements=["security_id"],
                set_={col: getattr(stmt.excluded, col) for col in METRICS_COLUMNS},
            )
            session.execute(stmt, metrics_rows)

    return len(metrics_rows)


if __name__ == "__main__":
    n = run_gold()
    print(f"wrote screener_metrics for {n} companies")
