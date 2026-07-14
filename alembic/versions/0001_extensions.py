"""enable timescaledb extension

Revision ID: 0001
Revises:
Create Date: 2026-07-14

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS timescaledb")
