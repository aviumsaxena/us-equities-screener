# Deploying Screener

The UI is 376 KB of static files and **useless on its own** — it needs the API,
Postgres (with TimescaleDB), and Redis behind it. So this is a four-piece deploy,
not static hosting.

The good news: the whole database is **~500 MB**. This fits on the cheapest tier
of anything.

---

## Read this first: the data licence

**SEC data is public domain.** Fundamentals, sectors, financial history — all fine
to publish.

**Price data is not.** Market caps, P/E, P/B, price charts and the price column
all derive from Polygon. Free tiers of market-data vendors generally permit you to
*use* the data but **prohibit redistributing it** — and serving it to the public
from a website is redistribution. Using it privately and publishing it are
different things legally.

Before going public, either:
- get a commercial/redistribution licence from the vendor, **or**
- publish only the SEC-derived side (screens on margins, ROE, growth, sector,
  financial history — all still work) and keep the price-derived columns private.

A public stock screener also normally carries a **"not investment advice"**
disclaimer. Neither of these is a code problem, and neither is mine to decide.

---

## Recommended shape: one VM, one origin

```
                 ┌────────── Caddy (TLS, :80/:443) ──────────┐
   browser ────► │  /            → web/dist  (static SPA)     │
                 │  /api/*       → api:8000  (FastAPI)        │
                 └───────────────────┬───────────────────────┘
                                     │  (internal network only)
                        ┌────────────┴────────────┐
                        │                         │
                   postgres:5432             redis:6379
                   (+TimescaleDB)              (cache)
```

Everything is served from **one origin**, which is the point:

- **No CORS.** The browser only ever talks to one host, so no cross-origin
  surface is opened at all. `CORS_ORIGINS` stays empty.
- **The database is never exposed.** Only Caddy publishes ports. Postgres and
  Redis are reachable only on the internal Docker network — that is the single
  most common way these deployments get owned.
- **TimescaleDB just works**, because you control the box. Many managed Postgres
  hosts will not let you install the extension, and `daily_prices` is a
  hypertable. (ARCHITECTURE.md §1 documents a fallback if you must use one:
  replace the hypertable with declarative range partitioning by year.)

Sizing: 2 vCPU / 4 GB RAM / 40 GB disk is plenty (~$15–25/mo on Hetzner or
DigitalOcean). The DB is ~500 MB; leave room for it to grow with price history.

---

## 1. Point a domain at the box

An `A` record for `screener.example.com` → your server's IP. Caddy provisions and
renews TLS automatically from that name; nothing else to do.

## 2. Configure secrets

```bash
cp .env.prod.example .env.prod
$EDITOR .env.prod          # SITE_ADDRESS, POSTGRES_PASSWORD, POLYGON_API_KEY
```

`.env.prod` is gitignored. Generate the DB password properly:

```bash
openssl rand -base64 32
```

## 3. Bring the stack up

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --build
```

Caddy gets a certificate on first boot; give it a few seconds.

## 4. Create the schema

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml \
  exec api alembic upgrade head
```

## 5. Load the data — restore, don't re-run the ETL

A cold ETL means re-downloading **2.7 GB** of SEC archives and re-crawling a
rate-limited price backfill (~90 min at 5 requests/min). Don't. Ship the database
you already built:

```bash
# on your laptop
docker exec screener-postgres-1 pg_dump -U screener -Fc screener > screener.dump
scp screener.dump you@server:~

# on the server
docker compose --env-file .env.prod -f docker-compose.prod.yml \
  exec -T postgres pg_restore -U screener -d screener --clean --if-exists < screener.dump
```

~500 MB, a couple of minutes.

## 6. Schedule the daily refresh

The pipeline is idempotent and re-runnable. On the host:

```cron
# 06:30 UTC daily — after the US close, before the morning
30 6 * * * cd /opt/screener && docker compose --env-file .env.prod -f docker-compose.prod.yml exec -T api python -m etl >> /var/log/screener-etl.log 2>&1
```

It refreshes fundamentals, prices and the gold tables, then bumps the Redis cache
version so the screen cache invalidates in O(1) (invariant #4).

---

## What's protected, and what isn't

**Protected:**
- `/screen` is **injection-safe by construction** — a whitelist maps each field to
  `{column, type, allowed ops}` and emits bound parameters only; user input is
  never interpolated into SQL (invariant #3, 21 tests).
- **Per-IP rate limiting** (60 req/min by default, `RATE_LIMIT_REQUESTS`). It
  *fails open*: if Redis dies, requests are allowed and a warning is logged — a
  cache outage degrades throttling rather than taking the API down. The trade-off
  is explicit: while Redis is down, there is no limit.
- Postgres and Redis are not internet-reachable. TLS is automatic. The API runs
  as a non-root user.

**Not protected — know this before you go public:**
- **There is no authentication.** Every endpoint is public. Anyone who finds the
  URL can screen. That is fine for a public tool and wrong for a private one.
- **No per-user quotas**, because there are no users. The IP limit is the only
  throttle, and it is trivially bypassed by a distributed client. Put Cloudflare
  in front if that matters.
- `TRUST_PROXY_HEADER=true` is set in the prod compose file. This is only safe
  **because** the API is not directly exposed — a client can forge
  `X-Forwarded-For`, so if you ever publish the API port directly, every attacker
  gets to spoof their IP and the rate limiter becomes decorative.

---

## Split-origin instead (Vercel/Netlify + a separate API)

Only if you want the frontend on a CDN. It costs you the CORS-free property:

```bash
# build the frontend against an absolute API origin
VITE_API_URL=https://api.example.com npm run build --prefix web

# and allow that origin on the API
CORS_ORIGINS=https://screener.example.com
```

Same-origin is simpler and strictly safer. Prefer it unless you have a reason.
