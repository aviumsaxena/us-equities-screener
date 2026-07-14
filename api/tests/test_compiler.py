"""Unit tests for the ScreenCompiler -- the SQL-injection boundary.

No DB needed: these compile predicate trees to SQLAlchemy expressions and
inspect the rendered SQL / bound params. The security guarantees under test:
unknown fields and disallowed operators are rejected, and user values only
ever appear as bound parameters (never inlined into the SQL text).
"""
from __future__ import annotations

import pytest
from sqlalchemy.dialects import postgresql

from api.compiler import (
    ScreenError,
    build_screen_query,
    compile_filter,
    decode_cursor,
    encode_cursor,
)
from api.models import Condition, Group


def _sql(node) -> str:
    expr = compile_filter(node)
    return str(expr.compile(dialect=postgresql.dialect()))


def _cond(field, op, value):
    return Condition(field=field, op=op, value=value)


# --- whitelist / injection boundary -----------------------------------------

def test_unknown_field_rejected():
    with pytest.raises(ScreenError, match="unknown field"):
        compile_filter(_cond("pe_ttm; DROP TABLE companies", "<", 20))


def test_injection_in_field_name_rejected():
    with pytest.raises(ScreenError, match="unknown field"):
        compile_filter(_cond("1=1 OR sector", "=", "x"))


def test_disallowed_operator_for_text_field():
    # text fields don't allow ordering operators
    with pytest.raises(ScreenError, match="not allowed"):
        compile_filter(_cond("sector", "<", "Technology"))


def test_disallowed_operator_for_boolean_field():
    with pytest.raises(ScreenError, match="not allowed"):
        compile_filter(_cond("rev_up_4q", ">", True))


def test_values_are_bound_not_interpolated():
    sql = _sql(_cond("sector", "=", "'; DROP TABLE companies; --"))
    # the malicious string must not appear inline; it becomes a bound param
    assert "DROP TABLE" not in sql
    assert "%(" in sql  # psycopg-style bound parameter placeholder


# --- operator / value shape validation --------------------------------------

def test_between_requires_two_element_list():
    with pytest.raises(ScreenError, match="BETWEEN"):
        compile_filter(_cond("pe_ttm", "BETWEEN", [10]))


def test_between_compiles():
    sql = _sql(_cond("pe_ttm", "between", [10, 20]))
    assert "BETWEEN" in sql.upper()


def test_in_requires_nonempty_list():
    with pytest.raises(ScreenError, match="IN requires"):
        compile_filter(_cond("sector", "IN", []))


def test_in_compiles():
    sql = _sql(_cond("sector", "in", ["Technology", "Energy"]))
    assert "IN (" in sql.upper()


def test_numeric_field_rejects_non_number():
    with pytest.raises(ScreenError, match="expected a number"):
        compile_filter(_cond("pe_ttm", "<", "cheap"))


def test_numeric_field_rejects_bool():
    with pytest.raises(ScreenError, match="expected a number"):
        compile_filter(_cond("pe_ttm", "<", True))


def test_numeric_field_accepts_numeric_string():
    # a numeric string coerces cleanly
    assert "pe_ttm" in _sql(_cond("pe_ttm", "<", "20"))


def test_boolean_field_rejects_non_bool():
    with pytest.raises(ScreenError, match="true/false"):
        compile_filter(_cond("profitable_5y", "=", "yes"))


def test_boolean_equality_compiles():
    # regression: eagerly building col<value for a bool used to raise at
    # construction time even when the chosen op is '='
    sql = _sql(_cond("profitable_5y", "=", True)).lower()
    assert "profitable_5y" in sql


def test_boolean_inequality_compiles():
    sql = _sql(_cond("rev_up_4q", "!=", False)).lower()
    assert "rev_up_4q" in sql


# --- nesting -----------------------------------------------------------------

def test_and_or_nesting_compiles():
    node = Group(op="AND", rules=[
        _cond("pe_ttm", "<", 20),
        Group(op="OR", rules=[
            _cond("sector", "=", "Information Technology"),
            _cond("revenue_growth_yoy", ">", 0.1),
        ]),
    ])
    sql = _sql(node).upper()
    assert "AND" in sql and "OR" in sql


def test_depth_limit_enforced():
    node = _cond("pe_ttm", "<", 20)
    for _ in range(8):
        node = Group(op="AND", rules=[node])
    with pytest.raises(ScreenError, match="too deep"):
        compile_filter(node)


def test_bad_group_operator_rejected():
    with pytest.raises(ScreenError, match="AND or OR"):
        compile_filter(Group(op="NAND", rules=[_cond("pe_ttm", "<", 20)]))


# --- pagination / cursor -----------------------------------------------------

def test_cursor_roundtrip():
    from decimal import Decimal
    cur = encode_cursor(Decimal("123456.78"), 42)
    sort_val, sid = decode_cursor(cur)
    assert sort_val == Decimal("123456.78")
    assert sid == 42


def test_invalid_cursor_rejected():
    with pytest.raises(ScreenError, match="invalid cursor"):
        decode_cursor("not-a-valid-cursor!!!")


def test_build_query_has_limit_plus_one_and_order():
    node = _cond("pe_ttm", "<", 20)
    stmt = build_screen_query(node, limit=100, cursor=None)
    sql = str(stmt.compile(dialect=postgresql.dialect())).upper()
    assert "ORDER BY" in sql
    assert "LIMIT" in sql
