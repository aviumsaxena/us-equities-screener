"""Silver: bronze companyfacts JSON -> normalized financial_facts rows.

Maps XBRL tags to standardized concepts (financial_concepts) and derives
each fact's real period from its own (start, end) dates rather than
trusting EDGAR's per-fact fy/fp labels. Those labels tag the *filing*, not
necessarily the fact: a 10-K's XBRL frame embeds prior-year comparatives
and "selected quarterly data" footnotes all under that filing's own fy/fp,
which otherwise collides unrelated periods into one bucket (surfaced on
NVDA, whose January fiscal year-end made the mismatch obvious -- its FY2010
10-K reports revenue for FYE Jan-2008, Jan-2009 *and* Jan-2010, plus
quarterly footnote figures, all labeled fy=2010/fp=FY). Grouping by the
fact's actual end date (+ duration bucket) instead keeps each real period
distinct; multiple entries for the same real period, sorted by filed date,
become restatement versions.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from etl import bulk
from etl.db import get_session
from etl.models import FinancialConcept, FinancialFact

log = logging.getLogger("etl.silver")

# matches the yearly partitions created in alembic/versions/0003_*
MIN_FISCAL_YEAR = 2016

ANNUAL_DURATION_DAYS = range(340, 386)
QUARTER_DURATION_DAYS = range(75, 105)


def _load_concepts() -> list[tuple[int, str, list[str]]]:
    with get_session() as session:
        rows = session.execute(
            select(FinancialConcept.concept_id, FinancialConcept.concept_key, FinancialConcept.xbrl_tags)
        ).all()
    return [tuple(r) for r in rows]


# The XBRL unit each concept must be reported in. Everything not listed is a
# monetary amount and must be USD.
_CONCEPT_UNIT = {
    "eps_diluted": "USD/shares",
    "shares_diluted": "shares",
}
_MONETARY_UNIT = "USD"


def expected_unit(concept_key: str) -> str:
    return _CONCEPT_UNIT.get(concept_key, _MONETARY_UNIT)


def _entries_for_tag(facts: dict, tag: str, unit: str) -> list[dict]:
    """Entries for one tag **in one unit**.

    Reading every unit indiscriminately silently corrupts foreign private
    issuers, which report the same tag in several currencies. Nomura (NMR)
    files `Revenues` under both JPY and USD: merging them read ¥4.76 trillion
    as $4.76 trillion, and its ¥118.99 EPS as $118.99 -- which is precisely why
    its P/E came out at 0.08 and its market cap at $29 QUADRILLION.

    A US-equity screener wants USD, so we take USD and drop the rest. A filer
    with no recent USD figures ends up with no metrics, which is the honest
    outcome -- far better than a number that is silently in the wrong currency.
    """
    node = facts.get("us-gaap", {}).get(tag)
    if not node:
        return []
    return list(node.get("units", {}).get(unit) or [])


def _entries_for_concept(facts: dict, xbrl_tags: list[str], concept_key: str) -> list[dict]:
    """Merges entries across every synonym tag for this concept -- filers
    commonly switch tags mid-history (e.g. ASC 606 adoption around 2018
    moved revenue from `Revenues` to `RevenueFromContractWithCustomer...`
    for many companies), so picking only the first tag with any data would
    silently drop recent periods. Only the concept's expected unit is read."""
    unit = expected_unit(concept_key)
    entries: list[dict] = []
    for tag in xbrl_tags:
        entries.extend(_entries_for_tag(facts, tag, unit))
    return entries


def _duration_days(start: Optional[str], end: str) -> Optional[int]:
    if not start:
        return None
    return (date.fromisoformat(end) - date.fromisoformat(start)).days


def _entry_type(e: dict) -> Optional[tuple[int, str]]:
    """Best-effort (confidence, fiscal_period) guess for a single entry.

    Highest confidence: duration and EDGAR's own fp *agree* (a real 90-day
    span actually labeled Q2, say) -- that's as trustworthy as this data
    gets. Lowest: duration alone, ignoring a disagreeing fp. This matters
    because filers disclose non-standard windows (trailing-twelve-months
    figures, e.g.) inside an otherwise-quarterly filing, dated the same as
    the filing's real quarter but spanning a full year -- SEC leaves fp set
    to the filing's actual quarter for those too, so a ~365-day span with
    fp='Q1' is the TTM figure, not a fiscal year (surfaced on AMZN, which
    tags trailing-twelve-month operating cash flow this way every 10-Q)."""
    duration = _duration_days(e.get("start"), e["end"])
    fp = e.get("fp")

    if duration in ANNUAL_DURATION_DAYS:
        return (3, "FY") if fp == "FY" else (1, "FY")
    if duration in QUARTER_DURATION_DAYS:
        calendar_q = f"Q{(date.fromisoformat(e['end']).month - 1) // 3 + 1}"
        return (3, fp) if fp in {"Q1", "Q2", "Q3", "Q4"} else (1, calendar_q)
    if duration is None:  # instant (balance-sheet) fact
        if (e.get("form") or "").startswith("10-K") or fp == "FY":
            return (2, "FY")
        if fp in {"Q1", "Q2", "Q3", "Q4"}:
            return (1, fp)
        return (1, "FY")
    return None  # odd duration (e.g. half-year YTD) -- doesn't fit our model


def _period_type(entries: list[dict]) -> Optional[str]:
    """Derives one FY-vs-quarter label for every entry sharing a real end
    date, from whichever entry gives the highest-confidence signal (see
    _entry_type) -- never trusting EDGAR's fp in isolation, since it tags
    the *filing's* reporting context and not necessarily the specific
    fact (see module docstring)."""
    candidates = [c for c in (_entry_type(e) for e in entries) if c is not None]
    if not candidates:
        return None
    return max(candidates, key=lambda c: c[0])[1]


def _consistent_with(fp: str, e: dict) -> bool:
    """After a group's fp is chosen, drop entries whose own duration
    contradicts it (e.g. the TTM entry once its sibling quarter wins)."""
    duration = _duration_days(e.get("start"), e["end"])
    if duration is None:
        return True
    return duration in ANNUAL_DURATION_DAYS if fp == "FY" else duration in QUARTER_DURATION_DAYS


def _is_impossible(e: dict) -> bool:
    """True for a fact whose period ends *after* the filing that reported it.

    That is a filer typo, not data: you cannot report revenue for a quarter, or
    a balance as of a date, that has not happened yet. Real examples in EDGAR --
    a 10-Q filed 2023-05-15 with `end=2031-09-25`, and one filed 2020-08-14
    reporting equity as of `2029-06-30`.

    The duration filter below already rejects the *period* form of this (an
    8-year "quarter" is not a clean annual or quarterly span), but **instant**
    balance-sheet facts have no duration, so nothing else would catch them --
    and a bogus year blows up the insert, because financial_facts is
    range-partitioned on exactly that year.
    """
    end = date.fromisoformat(e["end"])
    filed = e.get("filed")
    horizon = date.fromisoformat(filed) if filed else date.today()
    return end > horizon


def _group_by_period(entries: list[dict]) -> dict[str, list[dict]]:
    """Groups by the fact's own end date -- not EDGAR's fy/fp label -- so
    the same real-world period never splits across two labels, and distinct
    periods never collide into one (see module docstring)."""
    by_end: dict[str, list[dict]] = {}
    for e in entries:
        end, val = e.get("end"), e.get("val")
        if not end or val is None:
            continue
        if _is_impossible(e):
            continue
        by_end.setdefault(end, []).append(e)
    for group in by_end.values():
        group.sort(key=lambda e: e.get("filed") or "")
    return by_end


def build_fact_rows(
    payload: dict, security_id: int, concepts: list[tuple[int, str, list[str]]]
) -> list[dict]:
    """One company's companyfacts payload -> financial_facts rows."""
    facts = payload.get("facts") or {}
    rows: list[dict] = []

    for concept_id, concept_key, xbrl_tags in concepts:
        entries = _entries_for_concept(facts, xbrl_tags, concept_key)
        by_end = _group_by_period(entries)

        for end, group in by_end.items():
            fiscal_year = date.fromisoformat(end).year
            if fiscal_year < MIN_FISCAL_YEAR:
                continue
            fp = _period_type(group)
            if fp is None:
                continue
            clean_group = [e for e in group if _consistent_with(fp, e)]
            if not clean_group:
                continue
            version = 0
            last_val = None
            for e in clean_group:
                if last_val is not None and e["val"] == last_val:
                    continue  # same value re-filed, not a restatement
                version += 1
                last_val = e["val"]
                rows.append(dict(
                    security_id=security_id,
                    concept_id=concept_id,
                    fiscal_year=fiscal_year,
                    fiscal_period=fp,
                    version=version,
                    period_end=date.fromisoformat(end),
                    value=e["val"],
                    form_type=e.get("form"),
                    filed_date=date.fromisoformat(e["filed"]) if e.get("filed") else None,
                    restated=version > 1,
                ))

    return rows


def ensure_fact_partitions(years: set[int]) -> None:
    """Create any missing yearly partition of financial_facts.

    ARCHITECTURE.md §2.4 specifies one partition per year, "add yearly in ETL" --
    this is that step, and nothing was doing it. Without it the table hard-fails
    on any year outside the range the migration happened to create (2016-2027),
    so the pipeline would have broken in 2028 even with perfectly clean data.
    """
    sane = {y for y in years if MIN_FISCAL_YEAR <= y <= date.today().year + 1}
    if not sane:
        return
    with get_session() as session:
        for year in sorted(sane):
            session.execute(
                text(
                    f"CREATE TABLE IF NOT EXISTS financial_facts_{year} "
                    f"PARTITION OF financial_facts FOR VALUES FROM ({year}) TO ({year + 1})"
                )
            )


def _flush(rows: list[dict]) -> int:
    if not rows:
        return 0
    ensure_fact_partitions({r["fiscal_year"] for r in rows})
    stmt = insert(FinancialFact)
    stmt = stmt.on_conflict_do_update(
        index_elements=["security_id", "concept_id", "fiscal_year", "fiscal_period", "version"],
        set_={
            "period_end": stmt.excluded.period_end,
            "value": stmt.excluded.value,
            "form_type": stmt.excluded.form_type,
            "filed_date": stmt.excluded.filed_date,
            "restated": stmt.excluded.restated,
        },
    )
    with get_session() as session:
        session.execute(stmt, rows)
    return len(rows)


def transform_all(cik_to_id: dict[int, int], flush_every: int = 20_000) -> int:
    """Stream companyfacts out of the bulk archive -> financial_facts.

    Facts are accumulated across companies and flushed in batches: one INSERT
    per company means thousands of round trips across the full universe, and
    holding every row until the end would mean millions of dicts in memory.
    """
    archive = bulk.download(bulk.COMPANYFACTS_ZIP_URL, bulk.companyfacts_zip())
    concepts = _load_concepts()

    total = 0
    seen = 0
    pending: list[dict] = []

    for cik, payload in bulk.iter_members(archive, set(cik_to_id)):
        pending.extend(build_fact_rows(payload, cik_to_id[cik], concepts))
        seen += 1
        if len(pending) >= flush_every:
            total += _flush(pending)
            pending = []
            log.info("silver: %d companies, %d facts", seen, total)

    total += _flush(pending)
    log.info("silver: %d companies, %d facts (done)", seen, total)
    return total


if __name__ == "__main__":
    from sqlalchemy import select as _select

    from etl.models import Company

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    with get_session() as session:
        cik_to_id = dict(
            session.execute(
                _select(Company.cik, Company.security_id).where(Company.cik.isnot(None))
            ).all()
        )
    n = transform_all(cik_to_id)
    print(f"wrote {n} financial_facts rows for {len(cik_to_id)} companies")
