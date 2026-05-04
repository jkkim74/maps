"""add historical ohlcv table

Revision ID: 0003_historical_ohlcv
Revises: 0002_candidate_snapshot
Create Date: 2026-05-04
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_historical_ohlcv"
down_revision: Union[str, None] = "0002_candidate_snapshot"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "historical_ohlcv",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("ticker", sa.String(16), nullable=False),
        sa.Column("date", sa.Date, nullable=False),
        sa.Column("open", sa.Float, nullable=False),
        sa.Column("high", sa.Float, nullable=False),
        sa.Column("low", sa.Float, nullable=False),
        sa.Column("close", sa.Float, nullable=False),
        sa.Column("volume", sa.Integer, nullable=False),
        sa.Column("adj_close", sa.Float, nullable=True),
        sa.Column("source", sa.String(32), nullable=False, server_default="krx"),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("ticker", "date", name="uq_historical_ohlcv_ticker_date"),
    )
    op.create_index("ix_historical_ohlcv_ticker", "historical_ohlcv", ["ticker"])
    op.create_index("ix_historical_ohlcv_date", "historical_ohlcv", ["date"])


def downgrade() -> None:
    op.drop_index("ix_historical_ohlcv_date", table_name="historical_ohlcv")
    op.drop_index("ix_historical_ohlcv_ticker", table_name="historical_ohlcv")
    op.drop_table("historical_ohlcv")
