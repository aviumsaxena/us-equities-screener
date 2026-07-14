# Screener — US Equities Fundamental Screener

Screener.in, but for US stocks. A data-driven web app that ingests fundamentals + prices for **8,000+ US equities** (10 years of history) and lets users run fast custom logical screens, e.g. "P/E < 20 AND revenue growth > 10%".

**Full design lives in [ARCHITECTURE.md](./ARCHITECTURE.md) — treat it as the source of truth.** Update it whenever an architecture decision changes.

## Status
All three modules built and running against the **full ~7,636-filer SEC universe**
(previously a 20-ticker sample): 2.4M financial_facts, 5,789 companies with
computed metrics, 400k+ daily prices. A screen over the whole universe returns
in ~20ms.

Pipeline cost is near-flat in universe size: SEC bulk zips (one download each,
streamed per CIK) + Polygon grouped-daily (one call returns the whole US market).
Widening the universe again costs no new price API calls — bronze holds
whole-market snapshots and is replayed.

Known gaps (all correct-by-design NULLs — see ARCHITECTURE §6 for the full
data-quality table):
- **ADRs** (20-F/40-F filers, ~690) have NULL price multiples: the quoted price is
  per ADS but the filed share count is in ordinary shares, and the ratio isn't in
  SEC data. Their fundamentals (margins/ROE/growth) are fine.
- Multi-class issuers: only the primary class is screenable (one row per filer).
- `sector` is SIC-derived, **not licensed GICS** (~15/20 of a sample match GICS).
- `ev_ebitda`, `dividend_yield` still NULL (need D&A + cash concepts / dividends load).
- Free-tier prices give ~2 years of history, not the 10y target.

## Tech stack
- **Backend:** FastAPI (async), SQLAlchemy 2.0 / asyncpg, Pydantic
- **DB:** PostgreSQL 16 + TimescaleDB (hypertable for OHLCV)
- **Cache:** Redis (screen-result cache + external-API rate limiting)
- **Frontend:** React + Vite, TanStack Query, lightweight-charts
- **ETL:** Python, Prefect (plain cron for MVP); raw filings archived to S3/MinIO
- **Data sources:** SEC EDGAR bulk zips (fundamentals + SIC/exchange reference, free — one download each, streamed per CIK); Polygon grouped-daily EOD (prices, free tier — one call returns the whole US market; swap in `etl/extract/prices.py` alone)

## Module layout
- `web/` — React screener UI: query-builder, results grid, company page *(built)*
- `etl/` — extract → bronze → silver → gold pipeline; writes the serving tables *(built)*
- `api/` — FastAPI read layer over the gold tables + Redis; the `/screen` compiler *(built)*

Modules share only the DB contract (the gold tables) and must stay independently deployable.

## Design invariants — do not violate without updating ARCHITECTURE.md
1. **Precompute at ETL, never at query.** All ratios/growth are computed once per daily run and stored in `screener_metrics`. A screen is a single indexed `SELECT` — it never calls an external API and never recomputes a ratio.
2. **The screener queries `screener_metrics` only** (denormalized, 1 row/company). Normalized `financial_facts` is for storage/drill-down, not screening.
3. **The `/screen` compiler is whitelist-driven.** Map each field to `{column, type, allowed ops}`; emit parameterized SQL with bound params only. Never string-interpolate user input.
4. **Redis cache uses versioned keys** (`screen:v{N}:{hash}`); bump `N` on GOLD refresh for O(1) invalidation.
5. **History screens precompute boolean flags** at ETL time (e.g. `rev_up_4q`) so user-facing screens stay single-table filters.
6. Prices → Timescale hypertable (compressed + continuous aggregates). `financial_facts` → partitioned by `fiscal_year`. Use surrogate `security_id` everywhere (tickers get reused).

## Conventions
- Monetary/numeric values as `NUMERIC` in SQL — never floats.
- All timestamps UTC / `TIMESTAMPTZ`.
- Never commit secrets — use env vars / `.env` (gitignored); provide `.env.example`.

## Commands
Setup: `docker compose up -d` then `alembic upgrade head`. Install deps with
`pip install -e ".[api,dev]"` (omit extras for an ETL-only deploy).
- Infra: `docker compose up -d` (Postgres+TimescaleDB, Redis)
- Migrations: `alembic upgrade head`
- ETL run (full ~7.6k universe): `python -m etl`
- ETL run (20-ticker sample): `python -m etl --sample`
- Price backfill (one-off, resumable): `python -m etl.extract.prices --days 504`
- API dev server: `uvicorn api.main:app --reload`
- Tests: `pytest etl/tests/ api/tests/`
- Frontend dev: `npm install --prefix web && npm run dev --prefix web` (proxies /api → :8000)
