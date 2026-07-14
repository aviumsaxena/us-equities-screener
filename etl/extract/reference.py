"""Extract: company reference data (sector / industry / exchange) from SEC submissions.

Each filer's submissions record carries its SEC-assigned SIC code, that code's
description, and the exchanges it lists on. `industry` is SEC's own SIC
description; `sector` is derived from the SIC code (see etl/sic.py for why SIC
and not licensed GICS).

Read from the bulk `submissions.zip` rather than one API call per filer -- at
~7.6k companies the per-CIK path is thousands of sequential requests, where the
archive is a single download that every consumer then streams from.

Updates the `companies` dimension in place. GOLD denormalizes these columns into
screener_metrics, so run gold afterwards for sector screens to see them.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import bindparam, update

from etl import bulk
from etl.db import get_session
from etl.models import Company
from etl.sic import sic_to_sector

log = logging.getLogger("etl.reference")

BATCH = 1000


def parse_reference(payload: dict) -> dict:
    """SEC submissions JSON -> the companies columns we maintain."""
    exchanges = payload.get("exchanges") or []
    return {
        "sector": sic_to_sector(payload.get("sic")),
        "industry": payload.get("sicDescription") or None,
        "exchange": exchanges[0] if exchanges else None,
    }


def _apply(updates: list[dict]) -> None:
    if not updates:
        return
    stmt = (
        update(Company)
        .where(Company.security_id == bindparam("_sid"))
        .values(
            sector=bindparam("sector"),
            industry=bindparam("industry"),
            exchange=bindparam("exchange"),
        )
    )
    with get_session() as session:
        session.execute(stmt, updates)


def extract_reference(cik_to_id: Optional[dict[int, int]] = None) -> int:
    """Refresh sector/industry/exchange on `companies`. Returns rows updated."""
    if cik_to_id is None:
        from sqlalchemy import select

        with get_session() as session:
            cik_to_id = dict(
                session.execute(
                    select(Company.cik, Company.security_id).where(Company.cik.isnot(None))
                ).all()
            )

    archive = bulk.download(bulk.SUBMISSIONS_ZIP_URL, bulk.submissions_zip())

    updated = 0
    unmapped = 0
    pending: list[dict] = []

    for cik, payload in bulk.iter_members(archive, set(cik_to_id)):
        values = parse_reference(payload)
        if values["sector"] is None:
            unmapped += 1
        pending.append({"_sid": cik_to_id[cik], **values})
        if len(pending) >= BATCH:
            _apply(pending)
            updated += len(pending)
            pending = []
            log.info("reference: %d companies", updated)

    _apply(pending)
    updated += len(pending)

    if unmapped:
        log.info("reference: %d/%d companies have no SIC->sector mapping", unmapped, updated)
    log.info("reference: %d companies updated (done)", updated)
    return updated


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    n = extract_reference()
    print(f"updated reference data for {n} companies")
