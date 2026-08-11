"""add immutable stock analysis history with quote overlay

Revision ID: 0022_stock_analysis_history
Revises: 0021_analysis_pick_split_plan
Create Date: 2026-08-12
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0022_stock_analysis_history"
down_revision: Union[str, None] = "0021_analysis_pick_split_plan"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create append-only analysis snapshots and mutable quote overlay columns."""
    op.create_table(
        "stock_analysis_history",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("ticker", sa.String(16), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("market", sa.String(16), nullable=True),
        sa.Column("ref_date", sa.Date(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("narrative", sa.Text(), nullable=False, server_default=""),
        sa.Column("trade_plan", sa.JSON(), nullable=False),
        sa.Column("recommendation", sa.String(16), nullable=True),
        sa.Column("analyzed_price", sa.Float(), nullable=True),
        sa.Column("latest_price", sa.Float(), nullable=True),
        sa.Column("latest_reference_close", sa.Float(), nullable=True),
        sa.Column("latest_price_source", sa.String(32), nullable=True),
        sa.Column("price_refreshed_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_stock_analysis_history_created_at",
        "stock_analysis_history",
        ["created_at"],
    )
    op.create_index(
        "ix_stock_analysis_history_ticker",
        "stock_analysis_history",
        ["ticker"],
    )
    op.create_index(
        "ix_stock_analysis_history_ref_date",
        "stock_analysis_history",
        ["ref_date"],
    )


def downgrade() -> None:
    """Drop stock analysis history."""
    op.drop_index(
        "ix_stock_analysis_history_ref_date", table_name="stock_analysis_history"
    )
    op.drop_index(
        "ix_stock_analysis_history_ticker", table_name="stock_analysis_history"
    )
    op.drop_index(
        "ix_stock_analysis_history_created_at", table_name="stock_analysis_history"
    )
    op.drop_table("stock_analysis_history")
