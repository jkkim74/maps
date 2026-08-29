"""persist the upper-limit daily guard instead of inferring it

Revision ID: 0031_limit_up_guard
Revises: 0030_limit_up_pnl
Create Date: 2026-08-29
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0031_limit_up_guard"
down_revision: Union[str, None] = "0030_limit_up_pnl"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the durable per-trading-day guard record."""
    op.create_table(
        "limit_up_daily_guard",
        sa.Column("ref_date", sa.Date(), primary_key=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pattern_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("kosdaq_high", sa.Float(), nullable=True),
        sa.Column("halted_reasons", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    """Drop the guard record."""
    op.drop_table("limit_up_daily_guard")
