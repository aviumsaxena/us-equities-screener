"""fundamentals_periodic

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-14

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE fundamentals_periodic (
            security_id  BIGINT REFERENCES companies(security_id),
            period_end   DATE NOT NULL,
            fiscal_year  SMALLINT,
            fiscal_period TEXT,
            revenue      NUMERIC(20,2),
            net_income   NUMERIC(20,2),
            eps_diluted  NUMERIC(12,4),
            total_assets NUMERIC(20,2),
            total_equity NUMERIC(20,2),
            total_debt   NUMERIC(20,2),
            operating_cf NUMERIC(20,2),
            free_cf      NUMERIC(20,2),
            PRIMARY KEY (security_id, period_end)
        )
        """
    )
    op.execute("CREATE INDEX ix_fp_sec_period ON fundamentals_periodic (security_id, period_end DESC)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS fundamentals_periodic")
