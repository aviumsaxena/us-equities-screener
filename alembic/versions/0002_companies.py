"""companies

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-14

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE companies (
            security_id   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            cik           INTEGER UNIQUE,
            ticker        TEXT   NOT NULL,
            name          TEXT   NOT NULL,
            exchange      TEXT,
            sector        TEXT,
            industry      TEXT,
            currency      CHAR(3) DEFAULT 'USD',
            country       CHAR(2) DEFAULT 'US',
            is_active     BOOLEAN DEFAULT TRUE,
            listed_date   DATE,
            delisted_date DATE,
            created_at    TIMESTAMPTZ DEFAULT now(),
            updated_at    TIMESTAMPTZ DEFAULT now()
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX ux_companies_ticker_active ON companies (ticker) WHERE is_active")
    op.execute("CREATE INDEX ix_companies_sector ON companies (sector, industry)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS companies")
