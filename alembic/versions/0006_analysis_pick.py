"""add analysis_pick table (분석 워치리스트, SCR-19)

Revision ID: 0006_analysis_pick
Revises: c8a1f2b3d4e5
Create Date: 2026-06-25
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006_analysis_pick"
down_revision: Union[str, None] = "c8a1f2b3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 멱등 보강: startup의 Base.metadata.create_all()이 먼저 테이블을 만든 경우 건너뛴다.
    bind = op.get_bind()
    if "analysis_pick" in sa.inspect(bind).get_table_names():
        return

    op.create_table(
        "analysis_pick",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("ref_date", sa.Date, nullable=False),
        sa.Column("ticker", sa.String(16), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("market", sa.String(16), nullable=True),
        sa.Column("source", sa.String(16), nullable=False, server_default="analyze"),
        sa.Column("buy_price", sa.Float, nullable=True),
        sa.Column("target_price", sa.Float, nullable=True),
        sa.Column("stop_price", sa.Float, nullable=True),
        sa.Column("qty", sa.Integer, nullable=True),
        sa.Column("rationale", sa.Text, nullable=True),
        sa.Column("regime", sa.String(16), nullable=True),
        sa.Column("strategy_context", sa.String(128), nullable=True),
        sa.Column("strategy_trade_enabled", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("state", sa.String(16), nullable=False, server_default="WATCH"),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_analysis_pick_ref_date", "analysis_pick", ["ref_date"])
    op.create_index("ix_analysis_pick_ticker", "analysis_pick", ["ticker"])


def downgrade() -> None:
    op.drop_index("ix_analysis_pick_ticker", table_name="analysis_pick")
    op.drop_index("ix_analysis_pick_ref_date", table_name="analysis_pick")
    op.drop_table("analysis_pick")
