# US Equities Screener — System Architecture (v0.1)

Design target: fundamental + price data for **8,000+ US equities**, **10 years** of history, fast custom logical screens, minimal external API spend.

Guiding decisions in one line each:
- **Precompute at ingest, never at query.** Every ratio/growth figure is computed once per ETL run and stored. A screen is one indexed `SELECT` — it never touches an external API and never recomputes a ratio.
- **Split storage by access pattern.** Normalized long tables for *integrity & full statements*; a denormalized, indexed "gold" table for *screening*; a compressed hypertable for *prices*.
- **Three independent deployables** sharing only the DB contract: `etl/`, `api/`, `web/`.

---

## 1. Technical Architecture (text flowchart)

```
                        ┌─────────────────────────────────────────────┐
                        │            EXTERNAL DATA SOURCES              │
                        │  SEC EDGAR XBRL  (fundamentals, FREE bulk)    │
                        │  EOD price vendor (OHLCV, splits, dividends)  │
                        │  Reference data  (ticker↔CIK map, GICS)       │
                        └───────────────────┬─────────────────────────┘
                                            │  scheduled pull, INCREMENTAL via watermarks
                                            ▼
 ┌──────────────────────── ETL / DATA PIPELINE  (modular, orchestrated) ───────────────────────┐
 │                                                                                              │
 │  [Extract]  EDGAR daily-index + companyfacts.zip ;  EOD price files                          │
 │      │                                                                                       │
 │      ▼                                                                                       │
 │  BRONZE (raw)   ──►  object store (S3/MinIO) + raw JSONB landing tables                      │
 │      │          parse · type-cast · map XBRL tag → standard concept                          │
 │      ▼                                                                                       │
 │  SILVER (clean) ──►  normalized financial_facts · restatements versioned · splits applied    │
 │      │          compute derived ratios & growth (P/E, ROE, YoY, CAGR, boolean flags)         │
 │      ▼                                                                                       │
 │  GOLD (serving) ──►  screener_metrics (1 row/company)  +  fundamentals_periodic (per period) │
 └──────────────────────────────────────┬───────────────────────────────────────────────────┘
                                         │ writes
                                         ▼
      ┌───────────────────────────── STORAGE LAYER ─────────────────────────────┐
      │  PostgreSQL 16  +  TimescaleDB                                            │
      │    • daily_prices        hypertable · compressed · continuous aggregates  │
      │    • financial_facts     range-partitioned by fiscal_year                 │
      │    • screener_metrics    denormalized · indexed  → POWERS THE SCREENER    │
      │  Redis   • screen-result cache · hot company snapshots · API rate-limiter │
      │  Object store  • raw filings archive (replayable ETL)                     │
      └────────────────────────────────┬────────────────────────────────────────┘
                                        │  SQLAlchemy async / asyncpg · PgBouncer pool
                                        ▼
      ┌──────────────────────── BACKEND API  (FastAPI, async) ──────────────────┐
      │   POST /screen  → ScreenCompiler: JSON predicate tree → parameterized SQL │
      │   GET  /company/{id} · /prices · /financials  → cache-aside reads         │
      │   Auth · per-user rate limit · Pydantic response models                   │
      └────────────────────────────────┬────────────────────────────────────────┘
                                        │  REST/JSON  (TanStack Query client cache)
                                        ▼
      ┌──────────────────────── FRONTEND  (React + Vite) ───────────────────────┐
      │  Screener query-builder · virtualized results grid · saved screens        │
      │  Company page: statements · ratios · price chart (lightweight-charts)      │
      └──────────────────────────────────────────────────────────────────────────┘
```

### Tech-stack choices & the specific benefit

| Layer | Choice | Why (perf / cost) |
|---|---|---|
| Time-series | **TimescaleDB** (PG extension) | Native columnar **compression ≈ 90%** on OHLCV (10y × 8k × ~252 ≈ 20M rows) → direct storage-cost cut. **Continuous aggregates** precompute weekly/monthly bars so chart/"52-wk-high" reads don't scan raw ticks. It's a PG extension, so **no second datastore**. |
| Fundamentals source | **SEC EDGAR bulk** (`companyfacts.zip`, daily index) | Free and authoritative. **One bulk pull refreshes all 8k companies** vs 8k paid API calls — the single biggest external-cost lever. |
| Cache / throttle | **Redis** | Cache-aside for popular screens (data only changes daily) + **token-bucket** to keep any paid price API under its rate/credit ceiling. |
| API | **FastAPI + asyncpg** | Async I/O suits fan-out reads; Pydantic gives typed contracts and cheap request validation. |
| Orchestration | **Prefect** (or cron for MVP) | Pythonic, lighter than Airflow; retries + watermarked incremental loads. |
| Object store | **S3 / MinIO** | Keep raw filings so ETL is **replayable** — reprocess history without re-hitting sources. |

**Fallback if you can't install extensions** (some managed PG hosts): replace the hypertable with declarative range partitioning of `daily_prices` by year + manual monthly rollup tables. Everything else is stock Postgres.

---

## 2. Database Schema (PostgreSQL)

Design principle: **surrogate `security_id` everywhere** (tickers get reused and reassigned), **normalized facts for storage**, **denormalized gold for reads**.

### 2.1 Dimensions

```sql
CREATE TABLE companies (
    security_id   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    cik           INTEGER UNIQUE,                 -- SEC Central Index Key
    ticker        TEXT   NOT NULL,
    name          TEXT   NOT NULL,
    exchange      TEXT,                            -- NYSE, NASDAQ, ...
    sector        TEXT,                            -- GICS sector
    industry      TEXT,
    currency      CHAR(3) DEFAULT 'USD',
    country       CHAR(2) DEFAULT 'US',
    is_active     BOOLEAN DEFAULT TRUE,
    listed_date   DATE,
    delisted_date DATE,
    created_at    TIMESTAMPTZ DEFAULT now(),
    updated_at    TIMESTAMPTZ DEFAULT now()
);
CREATE UNIQUE INDEX ux_companies_ticker_active ON companies (ticker) WHERE is_active;
CREATE INDEX ix_companies_sector ON companies (sector, industry);

-- tickers change over time; keep history so old filings still resolve
CREATE TABLE ticker_history (
    security_id BIGINT REFERENCES companies(security_id),
    ticker      TEXT NOT NULL,
    valid_from  DATE NOT NULL,
    valid_to    DATE,
    PRIMARY KEY (security_id, valid_from)
);
```

### 2.2 Time series — OHLCV (TimescaleDB hypertable)

```sql
CREATE TABLE daily_prices (
    security_id BIGINT NOT NULL REFERENCES companies(security_id),
    dt          DATE   NOT NULL,
    open        NUMERIC(18,4),
    high        NUMERIC(18,4),
    low         NUMERIC(18,4),
    close       NUMERIC(18,4) NOT NULL,
    adj_close   NUMERIC(18,4),                     -- split/div adjusted
    volume      BIGINT,
    PRIMARY KEY (security_id, dt)
);
SELECT create_hypertable('daily_prices', 'dt', chunk_time_interval => INTERVAL '1 year');

-- compress chunks older than 90 days, grouped by security for columnar locality
ALTER TABLE daily_prices SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'security_id',
    timescaledb.compress_orderby   = 'dt'
);
SELECT add_compression_policy('daily_prices', INTERVAL '90 days');

-- auto-maintained monthly bars for charts / long-range screens
CREATE MATERIALIZED VIEW monthly_prices
WITH (timescaledb.continuous) AS
SELECT security_id,
       time_bucket('1 month', dt) AS month,
       first(open, dt) AS open,
       max(high)       AS high,
       min(low)        AS low,
       last(close, dt) AS close,
       sum(volume)     AS volume
FROM daily_prices
GROUP BY security_id, month;
```

### 2.3 Corporate actions

```sql
CREATE TABLE dividends (
    security_id BIGINT REFERENCES companies(security_id),
    ex_date     DATE NOT NULL,
    amount      NUMERIC(18,6) NOT NULL,
    PRIMARY KEY (security_id, ex_date)
);
CREATE TABLE splits (
    security_id BIGINT REFERENCES companies(security_id),
    ex_date     DATE NOT NULL,
    numerator   INTEGER,                           -- 4-for-1 => 4 / 1
    denominator INTEGER,
    PRIMARY KEY (security_id, ex_date)
);
```

### 2.4 Fundamentals — normalized long facts (partitioned by year)

A concept dictionary maps the thousands of raw XBRL tags onto a stable set of standardized metrics; facts are stored one row per company/period/concept.

```sql
CREATE TABLE financial_concepts (
    concept_id  SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    concept_key TEXT UNIQUE NOT NULL,              -- 'revenue','net_income','total_assets'
    statement   TEXT NOT NULL CHECK (statement IN ('IS','BS','CF')),
    xbrl_tags   TEXT[],                            -- {'Revenues','RevenueFromContractWithCustomer...'}
    sign        SMALLINT DEFAULT 1
);

CREATE TABLE financial_facts (
    security_id   BIGINT   NOT NULL REFERENCES companies(security_id),
    concept_id    SMALLINT NOT NULL REFERENCES financial_concepts(concept_id),
    fiscal_year   SMALLINT NOT NULL,
    fiscal_period TEXT     NOT NULL,               -- 'FY','Q1','Q2','Q3','Q4'
    period_end    DATE     NOT NULL,
    value         NUMERIC(28,4),
    form_type     TEXT,                            -- '10-K','10-Q'
    filed_date    DATE,
    restated      BOOLEAN  DEFAULT FALSE,
    version       SMALLINT DEFAULT 1,              -- restatement versioning
    PRIMARY KEY (security_id, concept_id, fiscal_year, fiscal_period, version)
) PARTITION BY RANGE (fiscal_year);

-- one partition per year keeps the 10-yr window prunable; add yearly in ETL
CREATE TABLE financial_facts_2016 PARTITION OF financial_facts FOR VALUES FROM (2016) TO (2017);
CREATE TABLE financial_facts_2017 PARTITION OF financial_facts FOR VALUES FROM (2017) TO (2018);
-- ... through the current year
CREATE TABLE financial_facts_2026 PARTITION OF financial_facts FOR VALUES FROM (2026) TO (2027);

-- "latest N periods of concept X for company Y" → index range scan
CREATE INDEX ix_ff_lookup ON financial_facts (security_id, concept_id, period_end DESC);
```

Rough size: 8k companies × ~44 periods (10y quarterly+annual) × ~120 concepts ≈ **40M rows** — comfortable for Postgres with partition pruning + the lookup index.

### 2.5 GOLD — the screener snapshot (denormalized, indexed)

One row per company, every screenable metric precomputed. **This is what the screener queries.** Identity columns are denormalized in so a screen needs no joins.

```sql
CREATE TABLE screener_metrics (
    security_id       BIGINT PRIMARY KEY REFERENCES companies(security_id),
    -- identity (denormalized for filter + display)
    ticker TEXT, name TEXT, sector TEXT, industry TEXT, exchange TEXT,
    -- market (price-driven, refreshed daily)
    price             NUMERIC(18,4),
    market_cap        NUMERIC(20,2),
    -- valuation
    pe_ttm            NUMERIC(12,4),
    pb                NUMERIC(12,4),
    ps_ttm            NUMERIC(12,4),
    ev_ebitda         NUMERIC(12,4),
    dividend_yield    NUMERIC(8,4),
    -- profitability
    gross_margin      NUMERIC(8,4),
    operating_margin  NUMERIC(8,4),
    net_margin        NUMERIC(8,4),
    roe               NUMERIC(8,4),
    roce              NUMERIC(8,4),
    -- growth (precomputed)
    revenue_ttm       NUMERIC(20,2),
    revenue_growth_yoy NUMERIC(8,4),
    eps_growth_yoy    NUMERIC(8,4),
    revenue_cagr_3y   NUMERIC(8,4),
    -- balance-sheet health
    debt_to_equity    NUMERIC(10,4),
    current_ratio     NUMERIC(10,4),
    interest_coverage NUMERIC(12,4),
    -- precomputed history flags (see §3.6)
    rev_up_4q         BOOLEAN,                     -- revenue grew 4 consecutive quarters
    profitable_5y     BOOLEAN,                     -- net income > 0 every year, 5y
    -- freshness
    price_asof        DATE,
    fundamentals_asof DATE,
    updated_at        TIMESTAMPTZ DEFAULT now()
);

-- one btree per high-selectivity screenable column → planner BitmapAnds multi-predicate screens
CREATE INDEX ix_sm_pe        ON screener_metrics (pe_ttm);
CREATE INDEX ix_sm_mktcap    ON screener_metrics (market_cap);
CREATE INDEX ix_sm_revgrowth ON screener_metrics (revenue_growth_yoy);
CREATE INDEX ix_sm_roe       ON screener_metrics (roe);
CREATE INDEX ix_sm_de        ON screener_metrics (debt_to_equity);
CREATE INDEX ix_sm_sector    ON screener_metrics (sector);
-- composite for the single most common screen (cheap + growing)
CREATE INDEX ix_sm_value_growth ON screener_metrics (pe_ttm, revenue_growth_yoy);
```

### 2.6 Per-period wide table (history screens)

Powers time-series predicates without pivoting the long table each query.

```sql
CREATE TABLE fundamentals_periodic (
    security_id  BIGINT REFERENCES companies(security_id),
    period_end   DATE NOT NULL,
    fiscal_year  SMALLINT,
    fiscal_period TEXT,
    revenue      NUMERIC(20,2),
    net_income   NUMERIC(20,2),
    eps_diluted  NUMERIC(12,4),
    total_assets NUMERIC(20,2),
    total_equity NUMERIC(20,2),
    total_debt   NUMERIC(20,2),
    operating_cf NUMERIC(20,2),
    free_cf      NUMERIC(20,2),
    PRIMARY KEY (security_id, period_end)
);
CREATE INDEX ix_fp_sec_period ON fundamentals_periodic (security_id, period_end DESC);
```

---

## 3. Screener Strategy — fast custom logical queries without external calls

### 3.1 The core move: precompute, don't compute-on-read
Every ratio and growth figure is produced in the GOLD ETL step and written to `screener_metrics`. A user screen is therefore a **single indexed filter over one table** — no EDGAR call, no per-row ratio math. P/E and market cap depend on price, so those columns are recomputed **daily** after the EOD price load even though earnings only change quarterly.

### 3.2 The scale insight that shapes everything
The *latest cross-section is only ~8,000 rows* (a few MB) — it lives entirely in Postgres `shared_buffers`. Any latest-snapshot screen, however many predicates, runs in **low-single-digit ms**; a full scan of 8k rows is microseconds. So **don't over-invest in indexing the latest table** — indexes here are for tidiness, not survival. The real scaling axes are:
1. **History/time-series screens** over the large fact tables (40M rows) — solved by partitioning + the periodic table + precomputed flags (§3.6).
2. **Derived-metric computation cost** — solved by doing it once per day in ETL, not per request.
3. **Concurrent read load** — solved by Redis cache-aside (§3.5) + PgBouncer pooling + (later) read replicas.

Calling this out matters because the naive instinct — "index every screener column heavily / shard the database" — spends effort on the axis that isn't the bottleneck.

### 3.3 Index strategy that actually matters
- **`screener_metrics`**: individual btrees on high-selectivity numeric columns; Postgres `BitmapAnd`s them for multi-predicate screens. Add a composite index only for empirically common filter *pairs* (e.g., `pe_ttm, revenue_growth_yoy`).
- **`financial_facts`**: partition prune by `fiscal_year` + `(security_id, concept_id, period_end DESC)` for "latest N periods."
- **`daily_prices`**: hypertable chunk exclusion + compression; "near 52-week high" reads the continuous aggregate, not raw ticks.

### 3.4 Safe dynamic query compiler (JSON predicate tree → parameterized SQL)
The frontend sends a predicate tree; the backend maps each `field` against a **whitelist** of `{column → type, allowed operators}` and emits SQL with **bound parameters only** (never string interpolation). The whitelist is what both prevents SQL injection and keeps the planner on indexed columns.

Request:
```jsonc
{ "op": "AND", "rules": [
    { "field": "pe_ttm",             "op": "<", "value": 20 },
    { "field": "revenue_growth_yoy", "op": ">", "value": 0.10 },
    { "field": "sector",             "op": "=", "value": "Information Technology" }
]}
```
Compiles to:
```sql
SELECT security_id, ticker, name, pe_ttm, revenue_growth_yoy, market_cap
FROM   screener_metrics
WHERE  pe_ttm < $1
  AND  revenue_growth_yoy > $2
  AND  sector = $3
ORDER  BY market_cap DESC
LIMIT  100;
```
Compiler rules: reject any `field` not in the whitelist; coerce `value` to the column's declared type; map `op` from an allowed set (`< <= = >= > BETWEEN IN`); nest `AND`/`OR` groups with parenthesization; always append a deterministic `ORDER BY` for keyset pagination.

### 3.5 Redis caching (data changes at most daily)
Cache-aside keyed by a **stable hash of the normalized screen JSON** → store the result (id list or full rows) with a TTL that expires at the next ETL run. Because prices refresh once/day and fundamentals quarterly, popular screens ("low P/E value", "high-ROE compounders") serve from Redis after first compute.

**O(1) invalidation:** prefix every cache key with a global version token (`screen:v{N}:{hash}`). When the daily GOLD refresh finishes, bump `N`. No key scanning, no stampede — the whole namespace is logically flushed instantly.

### 3.6 History / time-series screens (the hard ones)
For predicates that span periods — "revenue grew >10% for 4 straight quarters", "ROE > 15% every year for 5 years" — run a **windowed SQL over `fundamentals_periodic`** at ETL time and **materialize the boolean result** (`rev_up_4q`, `profitable_5y`) back into `screener_metrics`. The user-facing screen then stays a single-table filter. This is the pattern that keeps even complex historical logic O(indexed-scan) at query time.

### 3.7 Pagination
**Keyset pagination** on `(market_cap, security_id)` rather than `OFFSET` — stable and fast even deep into large result sets. `market_cap` is nullable (null until prices are wired in), so the implementation sorts and cursors on `COALESCE(market_cap, -1)` — a safe sentinel since a real cap is never negative — which keeps the cursor a clean non-null row-value comparison and degrades gracefully to `security_id` order while prices are stubbed.

### 3.8 MVP API scope (as-built)
The `api/` module implements §3.4–§3.7 plus a cache-aside `GET /company/{id}` drill-down (`screener_metrics` row + recent `fundamentals_periodic`) and a `GET /health` (DB + Redis liveness). Deferred until the pieces they depend on exist:
- **Auth + per-user rate limiting** (§1) — need a user model; not built yet. The Redis token-bucket throttle is for the *paid price API* (ETL side), separate from this.
- **Numeric transport** — storage stays `NUMERIC`, but JSON responses serialize numerics as floats for client ergonomics (a display concern, not the storage contract).
- The compiler builds SQLAlchemy Core expressions against whitelisted columns (identities from the whitelist, values as bound params) — a concrete realization of invariant #3, equivalent to the `$1`-param SQL sketched in §3.4.

---

## 4. How this maps to your four principles

- **Scalability** — access-pattern-split storage; hypertable + partitioning bound the large tables; the screener runs off a RAM-resident 8k-row snapshot; read scaling via cache + pooling + future replicas.
- **Cost-optimization** — free EDGAR bulk as the fundamentals source; one bulk pull vs 8k API calls; Timescale compression (~90%) cuts storage; Redis token-bucket caps paid-API spend; precompute-once-per-day eliminates repeated ratio computation.
- **Tech stack** — stays on FastAPI/PostgreSQL/React; the only additions (TimescaleDB, Redis, Prefect, MinIO) each earn their place with a specific perf/cost benefit and no second primary datastore.
- **Modularity** — `etl/`, `api/`, `web/` share only the GOLD table contract; any one can be rewritten without touching the others.

---

## 5. Suggested next steps
Done: the EDGAR extractor + silver/gold ETL, the `financial_concepts` seed, the `api/` `ScreenCompiler` + cache, the EOD price load, and the SIC reference-data load (all verified on a 20-ticker sample). Remaining:
1. **`web/`** — the React query-builder + results grid over `/screen`. The API contract it needs is now complete: valuation, growth, quality, and sector/exchange filters all return real data.
2. **Price history + adjustment** — the free tier caps us at ~100 trading days of *unadjusted* closes (§6). A paid tier / different vendor unlocks the 10y adjusted history that charts, 52-week-high screens, and the continuous aggregates assume.
3. **Concept coverage** — extend `financial_concepts` so sector-specific tagging (bank revenue) and the remaining metrics (`ev_ebitda` needs D&A + cash; `dividend_yield` needs a dividends load) aren't sparse.
4. **Scale out** — the pipeline has only ever run on 20 tickers. Widening to the full 8k universe is where the partitioning/hypertable/cache design actually gets exercised (and where Alpha Vantage's 25/day price cap becomes the binding constraint).

---

## 6. Data-source constraints (as-built)

**Fundamentals — SEC EDGAR `companyfacts`.** Free and authoritative, but three quirks shape `etl/silver/transform.py` (details in its docstrings):
- Per-fact `fy`/`fp` label the *filing*, not the fact — 10-Ks embed prior-year comparatives, 10-Qs embed TTM figures. Period identity is derived from each fact's own `(start, end)` dates, never the label.
- Filers switch XBRL tags mid-history (ASC 606 moved revenue tags ~2018), so every synonym tag for a concept is merged.
- `companyfacts` exposes only *undimensioned* facts. **Multi-share-class issuers** (BRK-B, V) report EPS and diluted share counts per class (behind a class axis), so they have no consolidated value — their `market_cap`/`pe_ttm`/`pb`/`ps_ttm` are correctly NULL rather than fabricated. Fixing this needs dimensional XBRL or a vendor share count.

**Reference data (sector / industry / exchange) — SEC `submissions`, not GICS.** §1 originally named GICS, but **GICS is proprietary** (S&P/MSCI licensed) and cannot be loaded freely. We instead use the **SIC** code SEC assigns to every filer, from the same `submissions` endpoint that gives us the exchange — free, no key, and it covers all 8k+ filers (the alternative, a vendor's sector endpoint, would need one call per ticker: ~320 days at Alpha Vantage's 25/day). `industry` is SEC's own SIC description; `sector` is mapped from the SIC code in `etl/sic.py` onto the familiar 11 GICS-style sector *names*.

The assignment is therefore SIC-based and approximate: SIC predates the digital economy, so on the 20-ticker sample **15/20 match GICS**, while GOOGL/META (SIC 7370 "Computer Programming") land in Information Technology rather than Communication Services, and V/MA (SIC 7389 "Business Services, NEC") land in Industrials rather than Financials. We deliberately do **not** hand-patch those: a manual override list doesn't scale to 8k names and amounts to reconstructing the licensed taxonomy. Dropping in a licensed GICS feed later just repopulates the same `companies.sector`/`industry` columns — no other module changes.

**Prices — Alpha Vantage free tier.** Chosen for MVP because the key is issued instantly with no email confirmation; the two other free no-key options are gated (stooq behind a JS proof-of-work bot check, Yahoo behind IP rate-limiting). Free-tier limits, all of which bound *history* rather than the latest close the gold metrics need:
- 25 requests/day, 5/minute → the extractor spaces calls and fetches each ticker once, landing raw JSON to bronze so re-running GOLD never re-fetches.
- `outputsize=full` is premium → we get ~100 trading days, not 10 years.
- `TIME_SERIES_DAILY` is unadjusted (adjusted close is premium) → `adj_close` is stored equal to `close`.

All of this is isolated in `etl/extract/prices.py`; swapping vendors touches that module and nothing else. The API key lives in `.env` (gitignored) and the module mutes httpx's INFO request logging, since the vendor supports no header auth and would otherwise leak the key into logs via the query string.
