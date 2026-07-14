"""Redis cache-aside with versioned keys (ARCHITECTURE.md §3.5).

Screen results are cached under `screen:v{N}:{hash}`. The version token N is
bumped by the ETL GOLD refresh (etl/cache.py) so the entire namespace is
invalidated in O(1) the moment fresh data lands -- no key scanning. A TTL is
kept only as a safety net.
"""
from __future__ import annotations

from typing import Optional

import redis.asyncio as aioredis

from api.config import settings

# NOTE: this key name is a cache contract shared with etl/cache.py. Keep the
# two in sync (they intentionally don't import each other, so the modules
# stay independently deployable).
VERSION_KEY = "screen:ver"

client: aioredis.Redis = aioredis.from_url(settings.redis_url, decode_responses=True)


async def current_version() -> int:
    """Current cache namespace version. Missing key => 0, so the first ETL
    bump (INCR 0 -> 1) actually rotates the namespace."""
    value = await client.get(VERSION_KEY)
    return int(value) if value is not None else 0


def make_key(version: int, digest: str) -> str:
    return f"screen:v{version}:{digest}"


async def get_cached(key: str) -> Optional[str]:
    return await client.get(key)


async def set_cached(key: str, payload: str) -> None:
    await client.set(key, payload, ex=settings.cache_ttl_seconds)


async def ping() -> bool:
    return bool(await client.ping())
