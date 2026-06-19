"""add kostolany columns — Steps 6-10

CandidateSnapshot: buy_stage, ai_contrarian_*, holding_type, price plan fields
SecurityMetadata: theme

Revision ID: a3f7c2d8e591
Revises: 1b89a330a0f6
Create Date: 2026-06-19 15:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a3f7c2d8e591"
down_revision: Union[str, None] = "1b89a330a0f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- CandidateSnapshot: Step 6 (buy_stage) ---
    op.add_column("candidate_snapshot", sa.Column("buy_stage", sa.Integer(), nullable=True))

    # --- CandidateSnapshot: Step 7 (AI contrarian) ---
    op.add_column("candidate_snapshot", sa.Column("ai_contrarian_score", sa.Float(), nullable=True))
    op.add_column("candidate_snapshot", sa.Column("ai_contrarian_opinion", sa.String(length=16), nullable=True))
    op.add_column("candidate_snapshot", sa.Column("ai_contrarian_reason", sa.Text(), nullable=True))
    op.add_column("candidate_snapshot", sa.Column("ai_contrarian_thesis", sa.Text(), nullable=True))
    op.add_column("candidate_snapshot", sa.Column("ai_contrarian_anti_thesis", sa.Text(), nullable=True))

    # --- CandidateSnapshot: Step 9 (holding_type) ---
    op.add_column("candidate_snapshot", sa.Column("holding_type", sa.String(length=16), nullable=True))

    # --- CandidateSnapshot: Step 10 (price plan) ---
    op.add_column("candidate_snapshot", sa.Column("technical_stop", sa.Float(), nullable=True))
    op.add_column("candidate_snapshot", sa.Column("thesis_stop", sa.Float(), nullable=True))
    op.add_column("candidate_snapshot", sa.Column("emergency_stop", sa.Float(), nullable=True))
    op.add_column("candidate_snapshot", sa.Column("trading_target", sa.Float(), nullable=True))
    op.add_column("candidate_snapshot", sa.Column("value_target", sa.Float(), nullable=True))
    op.add_column("candidate_snapshot", sa.Column("first_sell_price", sa.Float(), nullable=True))
    op.add_column("candidate_snapshot", sa.Column("final_sell_price", sa.Float(), nullable=True))

    # --- SecurityMetadata: Step 8 (theme) ---
    op.add_column("security_metadata", sa.Column("theme", sa.String(length=64), nullable=True))

    # --- CandidateSnapshot: valuation margin (Step 3, backfill) ---
    # Adds if not already present — wrapped in try/except for idempotency in SQLite.
    with op.batch_alter_table("candidate_snapshot") as batch_op:
        try:
            batch_op.add_column(sa.Column("valuation_margin_score", sa.Float(), nullable=True))
        except Exception:
            pass
        try:
            batch_op.add_column(sa.Column("valuation_margin_reason", sa.String(length=200), nullable=True))
        except Exception:
            pass


def downgrade() -> None:
    op.drop_column("security_metadata", "theme")
    op.drop_column("candidate_snapshot", "final_sell_price")
    op.drop_column("candidate_snapshot", "first_sell_price")
    op.drop_column("candidate_snapshot", "value_target")
    op.drop_column("candidate_snapshot", "trading_target")
    op.drop_column("candidate_snapshot", "emergency_stop")
    op.drop_column("candidate_snapshot", "thesis_stop")
    op.drop_column("candidate_snapshot", "technical_stop")
    op.drop_column("candidate_snapshot", "holding_type")
    op.drop_column("candidate_snapshot", "ai_contrarian_anti_thesis")
    op.drop_column("candidate_snapshot", "ai_contrarian_thesis")
    op.drop_column("candidate_snapshot", "ai_contrarian_reason")
    op.drop_column("candidate_snapshot", "ai_contrarian_opinion")
    op.drop_column("candidate_snapshot", "ai_contrarian_score")
    op.drop_column("candidate_snapshot", "buy_stage")
