"""financial_concepts, financial_facts (partitioned by fiscal_year)

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-14

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 2016 through 2027 covers 10y history plus headroom
PARTITION_YEARS = range(2016, 2028)


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE financial_concepts (
            concept_id  SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            concept_key TEXT UNIQUE NOT NULL,
            statement   TEXT NOT NULL CHECK (statement IN ('IS','BS','CF')),
            xbrl_tags   TEXT[],
            sign        SMALLINT DEFAULT 1
        )
        """
    )

    op.execute(
        """
        CREATE TABLE financial_facts (
            security_id   BIGINT   NOT NULL REFERENCES companies(security_id),
            concept_id    SMALLINT NOT NULL REFERENCES financial_concepts(concept_id),
            fiscal_year   SMALLINT NOT NULL,
            fiscal_period TEXT     NOT NULL,
            period_end    DATE     NOT NULL,
            value         NUMERIC(28,4),
            form_type     TEXT,
            filed_date    DATE,
            restated      BOOLEAN  DEFAULT FALSE,
            version       SMALLINT DEFAULT 1,
            PRIMARY KEY (security_id, concept_id, fiscal_year, fiscal_period, version)
        ) PARTITION BY RANGE (fiscal_year)
        """
    )

    for year in PARTITION_YEARS:
        op.execute(
            f"""
            CREATE TABLE financial_facts_{year}
            PARTITION OF financial_facts FOR VALUES FROM ({year}) TO ({year + 1})
            """
        )

    op.execute("CREATE INDEX ix_ff_lookup ON financial_facts (security_id, concept_id, period_end DESC)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS financial_facts")
    op.execute("DROP TABLE IF EXISTS financial_concepts")
