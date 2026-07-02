"""add market_regime_log table (장세 판정 이력 — 히스테리시스·floor 2일 확인)

Revision ID: 0010_market_regime_log
Revises: 0009_device_token
Create Date: 2026-07-02
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010_market_regime_log"
down_revision: Union[str, None] = "0009_device_token"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 멱등 보강: startup의 Base.metadata.create_all()이 먼저 테이블을 만든 경우 건너뛴다.
    bind = op.get_bind()
    if "market_regime_log" in sa.inspect(bind).get_table_names():
        return

    op.create_table(
        "market_regime_log",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("ref_date", sa.Date, nullable=False),
        sa.Column("raw_regime", sa.String(16), nullable=False),
        sa.Column("applied_regime", sa.String(16), nullable=False),
        sa.Column("up_count", sa.Integer, nullable=True),
        sa.Column("total_assets", sa.Integer, nullable=True),
        sa.Column("weekly_trend", sa.String(8), nullable=False, server_default="pass"),
        sa.Column("vol_regime", sa.String(8), nullable=False, server_default="normal"),
        sa.Column("floor_applied", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("breadth_pct", sa.Float, nullable=True),
        sa.Column("kospi_above_ma5w", sa.Boolean, nullable=True),
        sa.Column("kospi_above_ma10w", sa.Boolean, nullable=True),
        sa.Column("source", sa.String(32), nullable=False, server_default="scheduler"),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_market_regime_log_ref_date", "market_regime_log", ["ref_date"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_market_regime_log_ref_date", table_name="market_regime_log")
    op.drop_table("market_regime_log")
