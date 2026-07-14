"""FastAPI read layer over the GOLD tables (ARCHITECTURE.md §1).

Endpoints:
  POST /screen           -> whitelist-compiled predicate tree, cache-aside
  GET  /company/{id}     -> screener_metrics row + recent periodic history
  GET  /health           -> DB + Redis liveness

Auth and per-user rate limiting (mentioned in §1) are deferred until a user
model exists; see ARCHITECTURE.md §3.8.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from sqlalchemy import text

from api import cache
from api.compiler import ScreenError
from api.db import engine
from api.models import CompanyResponse, ScreenRequest, ScreenResponse
from api.service import get_company, run_screen


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await engine.dispose()
    await cache.client.aclose()


app = FastAPI(title="Screener API", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    db_ok = False
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False

    try:
        redis_ok = await cache.ping()
    except Exception:
        redis_ok = False

    status = "ok" if (db_ok and redis_ok) else "degraded"
    return {"status": status, "db": db_ok, "redis": redis_ok}


@app.post("/screen", response_model=ScreenResponse)
async def screen(req: ScreenRequest) -> ScreenResponse:
    try:
        return await run_screen(req)
    except ScreenError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/company/{security_id}", response_model=CompanyResponse)
async def company(security_id: int) -> CompanyResponse:
    result = await get_company(security_id)
    if result is None:
        raise HTTPException(status_code=404, detail="company not found")
    return result
