"""Tests for the Polygon grouped-daily parser.

No key and no network: these run against a fixture shaped like Polygon's
documented Grouped Daily response. The behaviour that matters is that the
whole-market payload gets filtered down to the tickers we actually track --
that filter is what lets bronze stay a complete market snapshot (so widening
the universe later costs zero API calls).
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from etl.extract.prices import build_symbol_index, parse_grouped, trading_days, vendor_symbols

# Shape per Polygon's Grouped Daily docs: T=ticker, o/h/l/c=OHLC, v=volume,
# t=ms epoch of the bar. The payload covers the entire US market.
PAYLOAD = {
    "status": "OK",
    "adjusted": True,
    "resultsCount": 4,
    "results": [
        {"T": "AAPL", "o": 314.5, "h": 320.1, "l": 313.0, "c": 317.31, "v": 43257804, "t": 1783402200000},
        {"T": "MSFT", "o": 388.0, "h": 392.4, "l": 387.1, "c": 390.99, "v": 18900000, "t": 1783402200000},
        # not in our universe -> must be dropped
        {"T": "ZZZZ", "o": 1.0, "h": 1.2, "l": 0.9, "c": 1.1, "v": 1000, "t": 1783402200000},
        # malformed: no close -> must be dropped rather than written as NULL
        {"T": "NVDA", "o": 200.0, "h": 205.0, "l": 199.0, "c": None, "v": 5000, "t": 1783402200000},
    ],
}

UNIVERSE = build_symbol_index({"AAPL": 9, "MSFT": 15, "NVDA": 1})


# --- ticker symbology: the bug that silently dropped BRK-B -------------------

def test_share_class_ticker_maps_to_vendor_dot_form():
    # SEC writes BRK-B; Polygon writes BRK.B. Both must resolve, or every
    # multi-class share silently gets no prices at all.
    assert vendor_symbols("BRK-B") == ["BRK-B", "BRK.B"]
    index = build_symbol_index({"BRK-B": 8})
    assert index["BRK-B"] == 8
    assert index["BRK.B"] == 8


def test_plain_ticker_has_no_extra_variants():
    assert vendor_symbols("AAPL") == ["AAPL"]


def test_parse_resolves_vendor_dot_symbol():
    payload = {
        "status": "OK",
        "results": [{"T": "BRK.B", "o": 495.0, "h": 499.0, "l": 494.0, "c": 496.85, "v": 3_000_000, "t": 1783402200000}],
    }
    rows = parse_grouped(payload, build_symbol_index({"BRK-B": 8}))
    assert len(rows) == 1
    assert rows[0]["security_id"] == 8
    assert rows[0]["close"] == Decimal("496.8500")


# --- universe filtering -----------------------------------------------------

def test_filters_to_our_universe():
    rows = parse_grouped(PAYLOAD, UNIVERSE)
    # ZZZZ is off-universe; NVDA has no close
    assert {r["security_id"] for r in rows} == {9, 15}


def test_maps_ohlcv_and_security_id():
    rows = parse_grouped(PAYLOAD, UNIVERSE)
    aapl = next(r for r in rows if r["security_id"] == 9)
    assert aapl["open"] == Decimal("314.5000")
    assert aapl["high"] == Decimal("320.1000")
    assert aapl["low"] == Decimal("313.0000")
    assert aapl["close"] == Decimal("317.3100")
    assert aapl["volume"] == 43257804


def test_close_is_used_for_adj_close():
    # adjusted=true, so the OHLC series is already split-adjusted
    rows = parse_grouped(PAYLOAD, UNIVERSE)
    assert all(r["close"] == r["adj_close"] for r in rows)


def test_converts_epoch_millis_to_date():
    # A daily bar's `t` is the start of the window in US Eastern (midnight or
    # 09:30 ET = 04:00-14:30 UTC), so the UTC date always equals the ET trading
    # date -- there is no off-by-one-day risk from reading it as UTC.
    rows = parse_grouped(PAYLOAD, UNIVERSE)
    assert all(isinstance(r["dt"], dt.date) for r in rows)
    assert rows[0]["dt"] == dt.date(2026, 7, 7)


def test_bar_without_close_is_dropped():
    rows = parse_grouped(PAYLOAD, UNIVERSE)
    assert all(r["security_id"] != 1 for r in rows)  # NVDA had close=None


def test_empty_payload_is_safe():
    # market holidays come back OK with no results
    assert parse_grouped({"status": "OK", "results": []}, UNIVERSE) == []
    assert parse_grouped({"status": "OK"}, UNIVERSE) == []


def test_trading_days_skips_weekends_newest_first():
    # 2026-07-13 is a Monday; walking back must skip Sat/Sun
    days = list(trading_days(dt.date(2026, 7, 13), 4))
    assert days == [
        dt.date(2026, 7, 13),  # Mon
        dt.date(2026, 7, 10),  # Fri
        dt.date(2026, 7, 9),   # Thu
        dt.date(2026, 7, 8),   # Wed
    ]
    assert all(d.weekday() < 5 for d in days)
