"""SEC bulk archives: download once, stream many.

At 20 tickers, one HTTP call per company was fine. At the real universe
(~7.6k CIKs) it is not: `companyfacts` runs 1-20 MB per company, so per-company
fetches mean thousands of requests and tens of GB of bronze.

SEC publishes the whole corpus as two zips instead -- exactly the "one bulk pull
refreshes all 8k companies" lever ARCHITECTURE.md §1 calls the single biggest
external-cost saving:

    companyfacts.zip   ~1.3 GB   every filer's XBRL facts, one JSON per CIK
    submissions.zip    ~1.4 GB   every filer's metadata (SIC, exchanges, name)

We keep the **zip itself** as the bronze archive and stream entries out of it on
demand. Nothing is ever expanded to disk, so bronze stays ~2.7 GB rather than
the tens of GB an exploded corpus would cost -- and the archive is still fully
replayable, which is the whole point of the bronze layer.
"""
from __future__ import annotations

import json
import logging
import zipfile
from pathlib import Path
from typing import Iterator, Optional

import httpx

from etl.config import settings

log = logging.getLogger("etl.bulk")

COMPANYFACTS_ZIP_URL = "https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip"
SUBMISSIONS_ZIP_URL = "https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip"


def _bulk_dir() -> Path:
    path = Path(settings.bronze_path) / "sec-bulk"
    path.mkdir(parents=True, exist_ok=True)
    return path


def companyfacts_zip() -> Path:
    return _bulk_dir() / "companyfacts.zip"


def submissions_zip() -> Path:
    return _bulk_dir() / "submissions.zip"


def download(url: str, dest: Path, force: bool = False) -> Path:
    """Stream a bulk archive to disk. Skips the download if it's already there,
    so re-running the pipeline never re-pulls gigabytes."""
    if dest.exists() and not force:
        log.info("%s already present (%.2f GB); skipping download", dest.name, dest.stat().st_size / 2**30)
        return dest

    tmp = dest.with_suffix(dest.suffix + ".part")
    headers = {"User-Agent": settings.sec_edgar_user_agent}
    with httpx.stream("GET", url, headers=headers, timeout=120.0, follow_redirects=True) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", 0))
        done = 0
        next_mark = 10
        with tmp.open("wb") as fh:
            for chunk in resp.iter_bytes(chunk_size=1 << 20):
                fh.write(chunk)
                done += len(chunk)
                if total:
                    pct = done * 100 // total
                    if pct >= next_mark:
                        log.info("%s: %d%% (%.2f GB)", dest.name, pct, done / 2**30)
                        next_mark = pct + 10
    tmp.replace(dest)  # atomic: a half-written archive never looks complete
    log.info("%s: downloaded %.2f GB", dest.name, dest.stat().st_size / 2**30)
    return dest


def _cik_member(cik: int) -> str:
    return f"CIK{cik:010d}.json"


def read_member(archive: Path, cik: int) -> Optional[dict]:
    """One CIK's JSON out of a bulk zip, or None if the filer isn't in it."""
    with zipfile.ZipFile(archive) as zf:
        try:
            with zf.open(_cik_member(cik)) as fh:
                return json.load(fh)
        except KeyError:
            return None


def iter_members(archive: Path, ciks: set[int]) -> Iterator[tuple[int, dict]]:
    """Stream (cik, payload) for the requested CIKs.

    Opens the zip once and pulls members out individually -- the archive is
    never expanded, so memory stays at one company's JSON at a time regardless
    of how large the universe is.
    """
    with zipfile.ZipFile(archive) as zf:
        available = set(zf.namelist())
        for cik in sorted(ciks):
            member = _cik_member(cik)
            if member not in available:
                continue  # filer has no XBRL facts / no submissions record
            with zf.open(member) as fh:
                try:
                    yield cik, json.load(fh)
                except json.JSONDecodeError as exc:
                    log.warning("CIK %s: malformed JSON in %s (%s)", cik, archive.name, exc)
