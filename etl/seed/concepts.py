"""Seeds financial_concepts: XBRL tag -> standardized concept mapping.

Covers the metrics needed for MVP screener_metrics ratios (§2.5/§2.6 of
ARCHITECTURE.md). Idempotent: re-running upserts on concept_key.
"""
from __future__ import annotations

from sqlalchemy.dialects.postgresql import insert

from etl.db import get_session
from etl.models import FinancialConcept

CONCEPTS = [
    dict(concept_key="revenue", statement="IS", sign=1, xbrl_tags=[
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "SalesRevenueNet",
    ]),
    dict(concept_key="cost_of_revenue", statement="IS", sign=1, xbrl_tags=[
        "CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold",
    ]),
    dict(concept_key="gross_profit", statement="IS", sign=1, xbrl_tags=["GrossProfit"]),
    dict(concept_key="operating_income", statement="IS", sign=1, xbrl_tags=["OperatingIncomeLoss"]),
    dict(concept_key="net_income", statement="IS", sign=1, xbrl_tags=["NetIncomeLoss", "ProfitLoss"]),
    dict(concept_key="eps_diluted", statement="IS", sign=1, xbrl_tags=["EarningsPerShareDiluted"]),
    dict(concept_key="shares_diluted", statement="IS", sign=1, xbrl_tags=[
        "WeightedAverageNumberOfDilutedSharesOutstanding",
    ]),
    dict(concept_key="interest_expense", statement="IS", sign=1, xbrl_tags=[
        "InterestExpense", "InterestExpenseDebt",
    ]),
    dict(concept_key="total_assets", statement="BS", sign=1, xbrl_tags=["Assets"]),
    dict(concept_key="current_assets", statement="BS", sign=1, xbrl_tags=["AssetsCurrent"]),
    dict(concept_key="total_liabilities", statement="BS", sign=1, xbrl_tags=["Liabilities"]),
    dict(concept_key="current_liabilities", statement="BS", sign=1, xbrl_tags=["LiabilitiesCurrent"]),
    dict(concept_key="total_equity", statement="BS", sign=1, xbrl_tags=[
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ]),
    dict(concept_key="long_term_debt", statement="BS", sign=1, xbrl_tags=["LongTermDebtNoncurrent"]),
    dict(concept_key="short_term_debt", statement="BS", sign=1, xbrl_tags=[
        "LongTermDebtCurrent", "ShortTermBorrowings",
    ]),
    dict(concept_key="operating_cf", statement="CF", sign=1, xbrl_tags=[
        "NetCashProvidedByUsedInOperatingActivities",
    ]),
    dict(concept_key="capex", statement="CF", sign=1, xbrl_tags=[
        "PaymentsToAcquirePropertyPlantAndEquipment",
    ]),
]


def seed() -> None:
    with get_session() as session:
        for concept in CONCEPTS:
            stmt = (
                insert(FinancialConcept)
                .values(**concept)
                .on_conflict_do_update(
                    index_elements=["concept_key"],
                    set_={"statement": concept["statement"], "xbrl_tags": concept["xbrl_tags"]},
                )
            )
            session.execute(stmt)


if __name__ == "__main__":
    seed()
    print(f"seeded {len(CONCEPTS)} financial_concepts")
