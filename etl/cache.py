"""Best-effort cache-version bump for the screener's Redis namespace.

The API caches screen results under `screen:v{N}:{hash}`; bumping N here after
a GOLD refresh invalidates the whole namespace in O(1) (ARCHITECTURE.md §3.5,
invariant #4). Kept best-effort: the ETL's job is the DB, so a missing or
unreachable Redis (or the `redis` package not installed in an ETL-only
deploy) must not fail the pipeline -- the API's cache TTL is the fallback.
"""
from __future__ import annotations

import logging

from etl.config import settings

log = logging.getLogger("etl.cache")

# Cache contract shared with api/cache.py -- keep the key name in sync (the
# modules intentionally don't import each other, per ARCHITECTURE.md §4).
VERSION_KEY = "screen:ver"


def bump_screen_cache_version() -> None:
    try:
        import redis  # lazy: optional in ETL-only deployments
    except ImportError:
        log.info("redis not installed; skipping cache-version bump")
        return

    try:
        client = redis.from_url(settings.redis_url)
        new_version = client.incr(VERSION_KEY)
        client.close()
        log.info("bumped screen cache version to %s", new_version)
    except Exception as exc:  # noqa: BLE001 -- best-effort, never fail the ETL
        log.warning("cache-version bump skipped: %s", exc)
