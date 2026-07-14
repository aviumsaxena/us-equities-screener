"""SQLAlchemy models for the tables ETL writes.

Mirrors ARCHITECTURE.md §2. Only the six tables the etl/ module touches for
the MVP pipeline: companies, financial_concepts, financial_facts,
fundamentals_periodic, screener_metrics, daily_prices. Physical DDL
(partitions, hypertable conversion, indexes) lives in alembic/versions/ as
raw SQL — these classes are for ORM reads/writes against tables that already
exist, not for autogenerate.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import List, Optional

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    Date,
    ForeignKey,
    Numeric,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


# The DB declares these columns BIGINT (see the migrations). A bare `Mapped[int]`
# would have SQLAlchemy infer 32-bit Integer and bind params as ::INTEGER, which
# silently works until a value crosses 2,147,483,647 -- then the INSERT dies with
# "integer out of range". That is not hypothetical: a penny stock (ADTX) traded
# 4.8 BILLION shares in a single session, 2.2x the int32 ceiling. Keep these
# explicit so the model matches the schema.
_BIGINT = BigInteger


class Company(Base):
    __tablename__ = "companies"

    security_id: Mapped[int] = mapped_column(_BIGINT, primary_key=True, autoincrement=True)
    cik: Mapped[Optional[int]] = mapped_column(unique=True)
    ticker: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    exchange: Mapped[Optional[str]] = mapped_column(Text)
    sector: Mapped[Optional[str]] = mapped_column(Text)
    industry: Mapped[Optional[str]] = mapped_column(Text)
    currency: Mapped[str] = mapped_column(String(3), server_default="USD")
    country: Mapped[str] = mapped_column(String(2), server_default="US")
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    listed_date: Mapped[Optional[dt.date]] = mapped_column(Date)
    delisted_date: Mapped[Optional[dt.date]] = mapped_column(Date)


class FinancialConcept(Base):
    __tablename__ = "financial_concepts"

    concept_id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=True)
    concept_key: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)  # 'IS' | 'BS' | 'CF'
    xbrl_tags: Mapped[List[str]] = mapped_column(ARRAY(Text))
    sign: Mapped[int] = mapped_column(SmallInteger, server_default="1")


class FinancialFact(Base):
    __tablename__ = "financial_facts"

    security_id: Mapped[int] = mapped_column(_BIGINT, ForeignKey("companies.security_id"), primary_key=True)
    concept_id: Mapped[int] = mapped_column(ForeignKey("financial_concepts.concept_id"), primary_key=True)
    fiscal_year: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    fiscal_period: Mapped[str] = mapped_column(Text, primary_key=True)  # 'FY','Q1'..'Q4'
    version: Mapped[int] = mapped_column(SmallInteger, primary_key=True, server_default="1")
    period_end: Mapped[dt.date] = mapped_column(Date, nullable=False)
    value: Mapped[Optional[Decimal]] = mapped_column(Numeric(28, 4))
    form_type: Mapped[Optional[str]] = mapped_column(Text)
    filed_date: Mapped[Optional[dt.date]] = mapped_column(Date)
    restated: Mapped[bool] = mapped_column(Boolean, server_default="false")


class FundamentalsPeriodic(Base):
    __tablename__ = "fundamentals_periodic"

    security_id: Mapped[int] = mapped_column(_BIGINT, ForeignKey("companies.security_id"), primary_key=True)
    period_end: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    fiscal_year: Mapped[Optional[int]] = mapped_column(SmallInteger)
    fiscal_period: Mapped[Optional[str]] = mapped_column(Text)
    revenue: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 2))
    net_income: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 2))
    eps_diluted: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 4))
    total_assets: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 2))
    total_equity: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 2))
    total_debt: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 2))
    operating_cf: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 2))
    free_cf: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 2))


class ScreenerMetrics(Base):
    __tablename__ = "screener_metrics"

    security_id: Mapped[int] = mapped_column(_BIGINT, ForeignKey("companies.security_id"), primary_key=True)
    # identity (denormalized)
    ticker: Mapped[Optional[str]] = mapped_column(Text)
    name: Mapped[Optional[str]] = mapped_column(Text)
    sector: Mapped[Optional[str]] = mapped_column(Text)
    industry: Mapped[Optional[str]] = mapped_column(Text)
    exchange: Mapped[Optional[str]] = mapped_column(Text)
    # market (price-driven; null until EOD vendor is wired in)
    price: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 4))
    market_cap: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 2))
    # valuation
    pe_ttm: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 4))
    pb: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 4))
    ps_ttm: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 4))
    ev_ebitda: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 4))
    dividend_yield: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))
    # profitability
    gross_margin: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))
    operating_margin: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))
    net_margin: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))
    roe: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))
    roce: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))
    # growth
    revenue_ttm: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 2))
    revenue_growth_yoy: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))
    eps_growth_yoy: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))
    revenue_cagr_3y: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))
    # balance-sheet health
    debt_to_equity: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4))
    current_ratio: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4))
    interest_coverage: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 4))
    # precomputed history flags
    rev_up_4q: Mapped[Optional[bool]] = mapped_column(Boolean)
    profitable_5y: Mapped[Optional[bool]] = mapped_column(Boolean)
    # freshness
    price_asof: Mapped[Optional[dt.date]] = mapped_column(Date)
    fundamentals_asof: Mapped[Optional[dt.date]] = mapped_column(Date)


class DailyPrice(Base):
    __tablename__ = "daily_prices"

    security_id: Mapped[int] = mapped_column(_BIGINT, ForeignKey("companies.security_id"), primary_key=True)
    dt: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    open: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 4))
    high: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 4))
    low: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 4))
    close: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    adj_close: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 4))
    volume: Mapped[Optional[int]] = mapped_column(_BIGINT)
