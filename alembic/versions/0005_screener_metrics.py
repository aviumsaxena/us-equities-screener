"""screener_metrics (gold)

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-14

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE screener_metrics (
            security_id       BIGINT PRIMARY KEY REFERENCES companies(security_id),
            ticker TEXT, name TEXT, sector TEXT, industry TEXT, exchange TEXT,
            price             NUMERIC(18,4),
            market_cap        NUMERIC(20,2),
            pe_ttm            NUMERIC(12,4),
            pb                NUMERIC(12,4),
            ps_ttm            NUMERIC(12,4),
            ev_ebitda         NUMERIC(12,4),
            dividend_yield    NUMERIC(8,4),
            gross_margin      NUMERIC(8,4),
            operating_margin  NUMERIC(8,4),
            net_margin        NUMERIC(8,4),
            roe               NUMERIC(8,4),
            roce              NUMERIC(8,4),
            revenue_ttm       NUMERIC(20,2),
            revenue_growth_yoy NUMERIC(8,4),
            eps_growth_yoy    NUMERIC(8,4),
            revenue_cagr_3y   NUMERIC(8,4),
            debt_to_equity    NUMERIC(10,4),
            current_ratio     NUMERIC(10,4),
            interest_coverage NUMERIC(12,4),
            rev_up_4q         BOOLEAN,
            profitable_5y     BOOLEAN,
            price_asof        DATE,
            fundamentals_asof DATE,
            updated_at        TIMESTAMPTZ DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_sm_pe        ON screener_metrics (pe_ttm)")
    op.execute("CREATE INDEX ix_sm_mktcap    ON screener_metrics (market_cap)")
    op.execute("CREATE INDEX ix_sm_revgrowth ON screener_metrics (revenue_growth_yoy)")
    op.execute("CREATE INDEX ix_sm_roe       ON screener_metrics (roe)")
    op.execute("CREATE INDEX ix_sm_de        ON screener_metrics (debt_to_equity)")
    op.execute("CREATE INDEX ix_sm_sector    ON screener_metrics (sector)")
    op.execute("CREATE INDEX ix_sm_value_growth ON screener_metrics (pe_ttm, revenue_growth_yoy)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS screener_metrics")
