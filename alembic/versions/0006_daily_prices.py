"""daily_prices (timescale hypertable)

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-14

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE daily_prices (
            security_id BIGINT NOT NULL REFERENCES companies(security_id),
            dt          DATE   NOT NULL,
            open        NUMERIC(18,4),
            high        NUMERIC(18,4),
            low         NUMERIC(18,4),
            close       NUMERIC(18,4) NOT NULL,
            adj_close   NUMERIC(18,4),
            volume      BIGINT,
            PRIMARY KEY (security_id, dt)
        )
        """
    )
    op.execute("SELECT create_hypertable('daily_prices', 'dt', chunk_time_interval => INTERVAL '1 year')")
    op.execute(
        """
        ALTER TABLE daily_prices SET (
            timescaledb.compress,
            timescaledb.compress_segmentby = 'security_id',
            timescaledb.compress_orderby   = 'dt'
        )
        """
    )
    op.execute("SELECT add_compression_policy('daily_prices', INTERVAL '90 days')")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS daily_prices")
