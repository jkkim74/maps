"""attribute limit-up realized P/L to the day it was booked

Revision ID: 0030_limit_up_pnl
Revises: 0029_limit_up_v1
Create Date: 2026-08-29
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0030_limit_up_pnl"
down_revision: Union[str, None] = "0029_limit_up_v1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the per-trading-day realized P/L ledger and guard restore columns."""
    op.add_column(
        "limit_up_session",
        sa.Column("realized_pnl_by_date", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    """Drop the per-day ledger."""
    op.drop_column("limit_up_session", "realized_pnl_by_date")
