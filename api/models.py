"""Pydantic request/response models for the API.

The filter is a recursive predicate tree: a Group (AND/OR of sub-nodes) whose
leaves are Conditions. `extra="forbid"` on both makes the Group/Condition
union unambiguous (a leaf has no `rules`, a group has no `field`) and rejects
junk keys with a clear 422.
"""
from __future__ import annotations

from typing import Any, List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field


class Condition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str
    op: str
    value: Any


class Group(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: str  # 'AND' | 'OR' (case-insensitive; validated in the compiler)
    rules: List[Union["Group", "Condition"]] = Field(min_length=1)


Group.model_rebuild()

FilterNode = Union[Group, Condition]


class ScreenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filter: FilterNode
    limit: int = Field(default=100, ge=1, le=500)
    cursor: Optional[str] = None


class ScreenRow(BaseModel):
    security_id: int
    ticker: Optional[str] = None
    name: Optional[str] = None
    sector: Optional[str] = None
    industry: Optional[str] = None
    exchange: Optional[str] = None
    price: Optional[float] = None
    market_cap: Optional[float] = None
    pe_ttm: Optional[float] = None
    pb: Optional[float] = None
    ps_ttm: Optional[float] = None
    roe: Optional[float] = None
    net_margin: Optional[float] = None
    revenue_growth_yoy: Optional[float] = None
    revenue_cagr_3y: Optional[float] = None
    debt_to_equity: Optional[float] = None
    current_ratio: Optional[float] = None
    rev_up_4q: Optional[bool] = None
    profitable_5y: Optional[bool] = None
    fundamentals_asof: Optional[str] = None


class ScreenResponse(BaseModel):
    results: List[ScreenRow]
    count: int
    next_cursor: Optional[str] = None
    cached: bool = False


class PeriodRow(BaseModel):
    period_end: str
    fiscal_year: Optional[int] = None
    fiscal_period: Optional[str] = None
    revenue: Optional[float] = None
    net_income: Optional[float] = None
    eps_diluted: Optional[float] = None
    total_assets: Optional[float] = None
    total_equity: Optional[float] = None
    total_debt: Optional[float] = None
    operating_cf: Optional[float] = None
    free_cf: Optional[float] = None


class CompanyResponse(BaseModel):
    company: dict
    history: List[PeriodRow]
    cached: bool = False
