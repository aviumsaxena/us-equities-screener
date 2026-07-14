"""ScreenCompiler: JSON predicate tree -> parameterized SQL (ARCHITECTURE.md §3.4).

Every user value becomes a *bound parameter* -- this module never string-
interpolates user input. It builds SQLAlchemy Core expressions against the
whitelisted columns in api/schema.py, so column identities come only from the
whitelist and values only from bound params. That whitelist + this coercion
are the SQL-injection boundary (invariant #3).

Pagination is keyset on (market_cap, security_id) (§3.7). market_cap is
nullable (null until prices are wired in), so we sort on
COALESCE(market_cap, -1) -- a safe sentinel since a real market cap is never
negative -- which turns the cursor into a clean non-null row-value
comparison that still works once prices land.
"""
from __future__ import annotations

import base64
import operator
from decimal import Decimal, InvalidOperation
from typing import Optional, Tuple

from sqlalchemy import Select, and_, func, or_, select, tuple_
from sqlalchemy.sql import ColumnElement

from api.models import Condition, FilterNode, Group
from api.schema import FIELDS, RESULT_COLUMNS, screener_metrics

MAX_DEPTH = 6
MAX_CONDITIONS = 64

# op token -> function that applies exactly that comparison. Using the
# operator module (rather than a dict of prebuilt `col < v` expressions)
# matters: SQLAlchemy rejects `col < True` at *construction* time, so
# building all six comparisons eagerly would blow up on boolean values even
# when the chosen op is '='.
SCALAR_OPS = {
    "=": operator.eq,
    "!=": operator.ne,
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
}

# sort sentinel: real market caps are >= 0, so -1 sorts nulls last under DESC
_SORT_KEY = func.coalesce(screener_metrics.c.market_cap, -1)
_TIEBREAK = screener_metrics.c.security_id


class ScreenError(ValueError):
    """Raised on any malformed / disallowed screen; surfaced as HTTP 400."""


def _coerce_scalar(kind: str, value) -> object:
    if kind == "numeric":
        # reject bools explicitly (bool is an int subclass in Python)
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise ScreenError(f"expected a number, got {value!r}")
        try:
            return Decimal(str(value))  # Decimal, never float -> honors NUMERIC contract
        except (InvalidOperation, ValueError):
            raise ScreenError(f"expected a number, got {value!r}")
    if kind == "boolean":
        if not isinstance(value, bool):
            raise ScreenError(f"expected true/false, got {value!r}")
        return value
    if kind == "text":
        if not isinstance(value, str):
            raise ScreenError(f"expected a string, got {value!r}")
        return value
    raise ScreenError(f"unknown field kind {kind!r}")


def _compile_condition(cond: Condition) -> ColumnElement:
    spec = FIELDS.get(cond.field)
    if spec is None:
        raise ScreenError(f"unknown field {cond.field!r}")

    op = cond.op.strip().upper()
    if op not in spec.ops:
        raise ScreenError(f"operator {cond.op!r} not allowed for field {cond.field!r}")

    col = spec.column

    if op == "BETWEEN":
        if not isinstance(cond.value, list) or len(cond.value) != 2:
            raise ScreenError("BETWEEN requires a [low, high] value")
        lo, hi = (_coerce_scalar(spec.kind, v) for v in cond.value)
        return col.between(lo, hi)

    if op == "IN":
        if not isinstance(cond.value, list) or not cond.value:
            raise ScreenError("IN requires a non-empty list value")
        return col.in_([_coerce_scalar(spec.kind, v) for v in cond.value])

    value = _coerce_scalar(spec.kind, cond.value)
    return SCALAR_OPS[op](col, value)


def _compile_node(node: FilterNode, depth: int, counter: list) -> ColumnElement:
    if isinstance(node, Condition):
        counter[0] += 1
        if counter[0] > MAX_CONDITIONS:
            raise ScreenError(f"too many conditions (max {MAX_CONDITIONS})")
        return _compile_condition(node)

    # Group
    if depth > MAX_DEPTH:
        raise ScreenError(f"filter nested too deep (max {MAX_DEPTH})")
    op = node.op.strip().upper()
    if op not in ("AND", "OR"):
        raise ScreenError(f"group operator must be AND or OR, got {node.op!r}")
    clauses = [_compile_node(child, depth + 1, counter) for child in node.rules]
    return and_(*clauses) if op == "AND" else or_(*clauses)


def compile_filter(node: FilterNode) -> ColumnElement:
    """Compile a predicate tree to a single parameterized WHERE expression."""
    return _compile_node(node, depth=0, counter=[0])


def encode_cursor(market_cap: Optional[Decimal], security_id: int) -> str:
    sort_val = market_cap if market_cap is not None else Decimal(-1)
    raw = f"{sort_val}:{security_id}".encode()
    return base64.urlsafe_b64encode(raw).decode()


def decode_cursor(cursor: str) -> Tuple[Decimal, int]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        sort_s, sid_s = raw.rsplit(":", 1)
        return Decimal(sort_s), int(sid_s)
    except (ValueError, InvalidOperation):
        raise ScreenError("invalid cursor")


def build_screen_query(node: FilterNode, limit: int, cursor: Optional[str]) -> Select:
    where = compile_filter(node)
    stmt = select(*RESULT_COLUMNS).where(where)

    if cursor:
        cur_sort, cur_id = decode_cursor(cursor)
        # keyset "next page" under (sort DESC, id DESC): strictly-less row value
        stmt = stmt.where(tuple_(_SORT_KEY, _TIEBREAK) < tuple_(cur_sort, cur_id))

    # fetch one extra to detect whether a further page exists
    return stmt.order_by(_SORT_KEY.desc(), _TIEBREAK.desc()).limit(limit + 1)
