# Screener — US Equities Fundamental Screener

Screener.in, but for US stocks. A data-driven web app that ingests fundamentals + prices for **8,000+ US equities** (10 years of history) and lets users run fast custom logical screens, e.g. "P/E < 20 AND revenue growth > 10%".

**Full design lives in [ARCHITECTURE.md](./ARCHITECTURE.md) — treat it as the source of truth.** Update it whenever an architecture decision changes.

## Status
`etl/` and `api/` built and verified end-to-end against the Docker stack on a
20-ticker sample. Price data is not yet wired (EOD vendor still TBD), so
price-derived columns (`price`, `market_cap`, `pe_ttm`, `pb`, `ps_ttm`,
`ev_ebitda`, `dividend_yield`) are stubbed NULL. `web/` not started.

## Tech stack
- **Backend:** FastAPI (async), SQLAlchemy 2.0 / asyncpg, Pydantic
- **DB:** PostgreSQL 16 + TimescaleDB (hypertable for OHLCV)
- **Cache:** Redis (screen-result cache + external-API rate limiting)
- **Frontend:** React + Vite, TanStack Query, lightweight-charts
- **ETL:** Python, Prefect (plain cron for MVP); raw filings archived to S3/MinIO
- **Data sources:** SEC EDGAR XBRL bulk (fundamentals, free); EOD price vendor (TBD)

## Module layout
- `etl/` — extract → bronze → silver → gold pipeline; writes the serving tables *(built)*
- `api/` — FastAPI read layer over the gold tables + Redis; the `/screen` compiler *(built)*
- `web/` — React screener UI *(not started)*

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
- ETL run (sample): `python -m etl --sample`
- API dev server: `uvicorn api.main:app --reload`
- Tests: `pytest api/tests/`
- Frontend dev: _TBD_
