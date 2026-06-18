"""add ai technical score to candidate snapshot

Revision ID: efca8676041a
Revises: 0005_stock_report_runs
Create Date: 2026-06-19 00:14:05.585358

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'efca8676041a'
down_revision: Union[str, None] = '0005_stock_report_runs'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('candidate_snapshot', sa.Column('ai_technical_score', sa.Float(), nullable=True))
    op.add_column('candidate_snapshot', sa.Column('ai_buy_price', sa.Float(), nullable=True))
    op.add_column('candidate_snapshot', sa.Column('ai_stop_price', sa.Float(), nullable=True))
    op.add_column('candidate_snapshot', sa.Column('ai_target_price', sa.Float(), nullable=True))
    op.add_column('candidate_snapshot', sa.Column('ai_analysis_memo', sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column('candidate_snapshot', 'ai_analysis_memo')
    op.drop_column('candidate_snapshot', 'ai_target_price')
    op.drop_column('candidate_snapshot', 'ai_stop_price')
    op.drop_column('candidate_snapshot', 'ai_buy_price')
    op.drop_column('candidate_snapshot', 'ai_technical_score')
