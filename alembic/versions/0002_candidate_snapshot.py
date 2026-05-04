"""add candidate snapshot table

Revision ID: 0002_candidate_snapshot
Revises: 0001_baseline
Create Date: 2026-05-04
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_candidate_snapshot"
down_revision: Union[str, None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "candidate_snapshot",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("ref_date", sa.Date, nullable=False),
        sa.Column("strategy_id", sa.String(64), nullable=False),
        sa.Column("ticker", sa.String(16), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("market", sa.String(16), nullable=False),
        sa.Column("factor_score", sa.Float, nullable=False, server_default="0"),
        sa.Column("trend_strength", sa.Float, nullable=False, server_default="0"),
        sa.Column("ts_bucket", sa.String(8), nullable=False, server_default="S3"),
        sa.Column("final_score", sa.Float, nullable=False, server_default="0"),
        sa.Column("weekly_pass", sa.Boolean, nullable=False, server_default="1"),
        sa.Column("estimated_qty", sa.Integer, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "ref_date",
            "strategy_id",
            "ticker",
            name="uq_candidate_snapshot_day_strategy_ticker",
        ),
    )
    op.create_index("ix_candidate_snapshot_ref_date", "candidate_snapshot", ["ref_date"])
    op.create_index("ix_candidate_snapshot_strategy_id", "candidate_snapshot", ["strategy_id"])
    op.create_index("ix_candidate_snapshot_ticker", "candidate_snapshot", ["ticker"])


def downgrade() -> None:
    op.drop_index("ix_candidate_snapshot_ticker", table_name="candidate_snapshot")
    op.drop_index("ix_candidate_snapshot_strategy_id", table_name="candidate_snapshot")
    op.drop_index("ix_candidate_snapshot_ref_date", table_name="candidate_snapshot")
    op.drop_table("candidate_snapshot")
