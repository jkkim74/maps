"""persist which +25% movers the upper-limit scanner refused to watch, and why

Revision ID: 0033_limit_up_scan_rejections
Revises: 0032_limit_up_ledger
Create Date: 2026-09-08
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0033_limit_up_scan_rejections"
down_revision: Union[str, None] = "0032_limit_up_ledger"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the per-ticker scan rejection map to the daily guard row."""
    op.add_column(
        "limit_up_daily_guard",
        sa.Column("scan_rejections", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    """Drop the scan rejection map."""
    op.drop_column("limit_up_daily_guard", "scan_rejections")
