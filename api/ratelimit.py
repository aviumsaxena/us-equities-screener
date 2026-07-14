"""Per-IP rate limiting, in Redis (ARCHITECTURE.md §1, §3.8).

`/screen` is a public, unauthenticated endpoint that runs a real database query.
The compiler makes it *safe* (whitelist + bound params), but nothing made it
*cheap* -- without a cap, one client can pin the API and the database. This is
the token bucket the architecture always called for; Redis was already there for
the screen cache.

Two deliberate choices:

* **Fails open.** If Redis is unreachable the request is allowed, with a warning.
  A cache outage should degrade throttling, not take the whole API down. The
  trade-off is explicit: while Redis is down, there is no limit.
* **Trusts `X-Forwarded-For` only when told to.** Behind the reverse proxy every
  request appears to come from the proxy, so without this the whole world shares
  one bucket. But a client can *send* that header, so honouring it when there is
  no proxy in front lets anyone forge an IP and bypass the limit entirely. Hence
  `TRUST_PROXY_HEADER`, off by default: safe when exposed directly, correct when
  deployed behind Caddy.
"""
from __future__ import annotations

import logging
import time

from fastapi import HTTPException, Request

from api import cache
from api.config import settings

log = logging.getLogger("api.ratelimit")

# these paths must stay reachable for monitoring / uptime checks
EXEMPT_PATHS = frozenset({"/health"})


def client_ip(request: Request) -> str:
    if settings.trust_proxy_header:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            # left-most entry is the original client
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def check(request: Request) -> None:
    """Raise 429 if this IP has exceeded its window. No-op when disabled."""
    if not settings.rate_limit_enabled or request.url.path in EXEMPT_PATHS:
        return

    window = settings.rate_limit_window_seconds
    limit = settings.rate_limit_requests

    ip = client_ip(request)
    bucket = int(time.time() // window)
    key = f"rl:{ip}:{bucket}"

    try:
        used = await cache.client.incr(key)
        if used == 1:
            await cache.client.expire(key, window)
    except Exception as exc:  # noqa: BLE001 -- fail open, see module docstring
        log.warning("rate limiter unavailable, allowing request: %s", exc)
        return

    if used > limit:
        retry_after = window - int(time.time() % window)
        raise HTTPException(
            status_code=429,
            detail=f"rate limit exceeded ({limit} requests per {window}s)",
            headers={"Retry-After": str(retry_after)},
        )
