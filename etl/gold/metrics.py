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

import logging
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

log = logging.getLogger("etl.gold")

# companies per pass; bounds peak memory independent of universe size
CHUNK_SIZE = 500

# BRK-A, the priciest US share, earns ~$40k/share. Past $100k it's a mis-tag.
MAX_PLAUSIBLE_EPS = Decimal(100_000)

# No listed company has anywhere near a trillion shares (the biggest counts are
# ~1e10, and extreme penny stocks reach ~1e11). Nomura filed a diluted share
# count of 3,041,190,068,000,000 -- 3 QUADRILLION, a million times its real ~3bn.
MAX_PLAUSIBLE_SHARES = Decimal(10) ** 12

# What each NUMERIC(p,s) column can actually hold: 10^(p-s).
#
# Ratios blow past these whenever the denominator is near zero, which is routine
# across ~7.6k filers: a shell with $1 of revenue and a $1M loss has a "net
# margin" of -1,000,000, and net_margin is NUMERIC(8,4) -- max 9,999.9999. That
# doesn't store a silly number, it aborts the INSERT and takes the run with it.
# A metric too large for its column is meaningless anyway, so it becomes NULL:
# garbage in, NULL out, never a crash.
_COLUMN_LIMITS: dict[str, Decimal] = {
    # NUMERIC(8,4)
    "gross_margin": Decimal(10) ** 4,
    "operating_margin": Decimal(10) ** 4,
    "net_margin": Decimal(10) ** 4,
    "roe": Decimal(10) ** 4,
    "roce": Decimal(10) ** 4,
    "revenue_growth_yoy": Decimal(10) ** 4,
    "eps_growth_yoy": Decimal(10) ** 4,
    "revenue_cagr_3y": Decimal(10) ** 4,
    "dividend_yield": Decimal(10) ** 4,
    # NUMERIC(10,4)
    "debt_to_equity": Decimal(10) ** 6,
    "current_ratio": Decimal(10) ** 6,
    # NUMERIC(12,4)
    "pe_ttm": Decimal(10) ** 8,
    "pb": Decimal(10) ** 8,
    "ps_ttm": Decimal(10) ** 8,
    "ev_ebitda": Decimal(10) ** 8,
    "interest_coverage": Decimal(10) ** 8,
    "eps_diluted": Decimal(10) ** 8,
    # NUMERIC(18,4) / NUMERIC(20,2)
    "price": Decimal(10) ** 14,
    "market_cap": Decimal(10) ** 18,
    "revenue_ttm": Decimal(10) ** 18,
    "revenue": Decimal(10) ** 18,
    "net_income": Decimal(10) ** 18,
    "total_assets": Decimal(10) ** 18,
    "total_equity": Decimal(10) ** 18,
    "total_debt": Decimal(10) ** 18,
    "operating_cf": Decimal(10) ** 18,
    "free_cf": Decimal(10) ** 18,
}


def _null_unstorable(row: dict) -> dict:
    """NULL out any value too large for its destination column (see _COLUMN_LIMITS)."""
    for column, limit in _COLUMN_LIMITS.items():
        value = row.get(column)
        if value is not None and abs(value) >= limit:
            row[column] = None
    return row


def _latest_facts(session, security_ids: list[int]) -> list:
    """Latest version of every fact, for one chunk of companies.

    The restatement dedup is done by Postgres (DISTINCT ON + version DESC), not
    in Python. Pulling the whole table and deduping client-side worked at 20
    tickers but would drag several GB into memory across the full ~7.6k-company
    universe -- hence both the DISTINCT ON and the chunking by security_id.
    """
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
        .where(FinancialFact.security_id.in_(security_ids))
        .distinct(
            FinancialFact.security_id,
            FinancialConcept.concept_key,
            FinancialFact.fiscal_year,
            FinancialFact.fiscal_period,
        )
        .order_by(
            FinancialFact.security_id,
            FinancialConcept.concept_key,
            FinancialFact.fiscal_year,
            FinancialFact.fiscal_period,
            FinancialFact.version.desc(),  # DISTINCT ON keeps this first row
        )
    )
    return session.execute(stmt).all()


def _foreign_private_issuers(session) -> set:
    """security_ids whose fundamentals come from 20-F / 40-F filings.

    These trade in the US as ADRs: the quoted price is per **ADS**, while the
    share count they file is in **ordinary shares**, and the ADS:ordinary ratio
    is nowhere in SEC's data. So every price multiple is wrong by exactly that
    ratio -- Alibaba is 1 ADS : 8 ordinary shares, which is why it showed a
    $2,161B market cap and a 140x P/E against a true ~$300B and ~17x, landing it
    6th-largest in the universe.

    We can't compute these without the ratio, so we don't pretend to: market_cap
    and the price multiples are left NULL. Their *fundamentals* -- margins, ROE,
    growth -- involve no price and stay perfectly good, so ADRs remain fully
    screenable on everything except valuation.
    """
    stmt = (
        select(FinancialFact.security_id)
        .where(FinancialFact.form_type.in_(("20-F", "40-F")))
        .distinct()
    )
    return {row[0] for row in session.execute(stmt)}


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
        if r.concept_key == "eps_diluted" and not _plausible_eps(r.value):
            continue
        bucket = out[r.security_id][r.period_end]
        bucket[r.concept_key] = r.value
        bucket["period_end"] = r.period_end
        bucket["fiscal_year"] = r.fiscal_year
        if bucket.get("fiscal_period") != "FY":
            bucket["fiscal_period"] = r.fiscal_period
    return out


def _plausible_eps(value: Optional[Decimal]) -> bool:
    """Reject an EPS that is obviously a mis-tagged share count.

    Filers do this: United States Antimony (UAMY) tagged
    `EarningsPerShareDiluted` with 69,697,150 / 92,711,336 / 107,260,472 -- under
    the correct USD/shares unit -- while its real EPS is about -$0.01. Left in,
    the largest overflows eps_diluted's NUMERIC(12,4) and kills the run, and the
    ones that *do* fit silently poison pe_ttm and eps_growth_yoy, which is worse.

    BRK-A, the highest-priced share in the US market, earns ~$40k/share, so
    anything past $100k/share is not earnings.
    """
    return value is None or abs(value) <= MAX_PLAUSIBLE_EPS


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
    return [_null_unstorable(r) for r in rows]


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
    security_id: int,
    periods: dict,
    company: Company,
    price_row: Optional[tuple] = None,
    is_adr: bool = False,
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
        _apply_price_metrics(
            row, price_row, revenue=None, total_equity=None, eps=None, shares=None,
            net_income=None, is_adr=is_adr,
        )
        return _null_unstorable(row)

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
        net_income=net_income,
        is_adr=is_adr,
    )
    return _null_unstorable(row)


def _validated_shares(
    shares: Optional[Decimal], net_income: Optional[Decimal], eps: Optional[Decimal]
) -> Optional[Decimal]:
    """Reject a share count that contradicts the company's own EPS and net income.

    By definition shares ~= net_income / EPS, so a filer that reports all three
    can be checked against itself. They do get it wrong: Bicara (BCAX) filed
    54,676,896,000 diluted shares for FY2025 -- 1,000x its true ~54.7M -- which
    valued a small biotech at **$1.5 trillion**, third-largest company in the
    universe. Its own EPS (-2.52) and net income imply the correct count.

    When the filed count and the implied one disagree by more than 10x, we don't
    silently pick one: market_cap is left NULL. A missing market cap is obvious;
    a fabricated one is not.

    The cross-check needs EPS and net income to compare against, which a filer
    may not report in USD -- so an absolute ceiling backstops it. That is not
    belt-and-braces: Nomura has no recent *USD* earnings, so the cross-check had
    nothing to work with and trusted its 3-quadrillion share count, valuing the
    company at $29 QUADRILLION. A guard that only fires when other data happens
    to exist is not a guard.
    """
    if shares is None or shares <= 0 or shares > MAX_PLAUSIBLE_SHARES:
        return None
    if net_income is None or eps is None or eps == 0:
        return shares  # nothing to cross-check against; the ceiling above stands
    implied = abs(net_income / eps)
    if implied == 0:
        return shares
    ratio = shares / implied
    return None if (ratio > 10 or ratio < Decimal("0.1")) else shares


def _apply_price_metrics(
    row, price_row, *, revenue, total_equity, eps, shares, net_income, is_adr=False
) -> None:
    """Fill price-derived columns from the latest close + latest fundamentals.
    Left NULL when price or the needed fundamental is missing."""
    if price_row is None:
        return
    price, price_asof = price_row
    # the ADR's quoted price is real and useful; it just can't be combined with a
    # per-ordinary-share count (see _foreign_private_issuers)
    row["price"] = price
    row["price_asof"] = price_asof
    if price is None or is_adr:
        return

    shares = _validated_shares(shares, net_income, eps)
    market_cap = price * shares if shares is not None else None
    row["market_cap"] = market_cap
    # PE only where earnings are positive; a negative/zero P/E isn't meaningful
    row["pe_ttm"] = _safe_div(price, eps) if (eps is not None and eps > 0) else None
    row["ps_ttm"] = _safe_div(market_cap, revenue)
    row["pb"] = _safe_div(market_cap, total_equity)


def _flush(session, periodic_rows: list[dict], metrics_rows: list[dict]) -> None:
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


def run_gold(chunk_size: int = CHUNK_SIZE) -> int:
    """Recompute the gold tables for every company.

    Streams the universe in chunks so peak memory is bounded by `chunk_size`
    companies, not by the size of financial_facts -- the whole table is millions
    of rows once the universe is the real ~7.6k companies.
    """
    with get_session() as session:
        companies = {c.security_id: c for c in session.execute(select(Company)).scalars()}
        prices = _latest_prices(session)
        adrs = _foreign_private_issuers(session)  # one row per company; small

    security_ids = sorted(companies)
    written = 0

    for start in range(0, len(security_ids), chunk_size):
        chunk = security_ids[start : start + chunk_size]

        with get_session() as session:
            facts = _latest_facts(session, chunk)

        pivoted = _pivot(facts)

        periodic_rows: list[dict] = []
        metrics_rows: list[dict] = []
        for security_id, periods in pivoted.items():
            company = companies.get(security_id)
            if company is None:
                continue
            periodic_rows.extend(_build_fundamentals_periodic_rows(security_id, periods))
            metrics_rows.append(
                _build_screener_metrics_row(
                    security_id, periods, company, prices.get(security_id),
                    is_adr=security_id in adrs,
                )
            )

        with get_session() as session:
            _flush(session, periodic_rows, metrics_rows)

        written += len(metrics_rows)
        log.info("gold: %d/%d companies", min(start + chunk_size, len(security_ids)), len(security_ids))

    return written


if __name__ == "__main__":
    n = run_gold()
    print(f"wrote screener_metrics for {n} companies")
