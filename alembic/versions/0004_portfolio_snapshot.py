"""add portfolio snapshot table

Revision ID: 0004_portfolio_snapshot
Revises: 0003_historical_ohlcv
Create Date: 2026-05-05
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_portfolio_snapshot"
down_revision: Union[str, None] = "0003_historical_ohlcv"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "portfolio_snapshot",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("ref_date", sa.Date, nullable=False),
        sa.Column("source", sa.String(32), nullable=False, server_default="broker"),
        sa.Column("total_assets", sa.Float, nullable=False),
        sa.Column("cash", sa.Float, nullable=False, server_default="0"),
        sa.Column("positions_value", sa.Float, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("ref_date", "source", name="uq_portfolio_snapshot_day_source"),
    )
    op.create_index("ix_portfolio_snapshot_ref_date", "portfolio_snapshot", ["ref_date"])


def downgrade() -> None:
    op.drop_index("ix_portfolio_snapshot_ref_date", table_name="portfolio_snapshot")
    op.drop_table("portfolio_snapshot")
