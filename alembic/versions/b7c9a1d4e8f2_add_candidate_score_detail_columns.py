"""add candidate score detail columns

Revision ID: b7c9a1d4e8f2
Revises: a3f7c2d8e591
Create Date: 2026-06-19 18:50:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "b7c9a1d4e8f2"
down_revision: Union[str, None] = "a3f7c2d8e591"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("candidate_snapshot", sa.Column("score_type", sa.String(length=32), nullable=True))
    op.add_column("candidate_snapshot", sa.Column("strategy_type", sa.String(length=32), nullable=True))
    op.add_column("candidate_snapshot", sa.Column("component_scores", sa.JSON(), nullable=True))
    op.add_column("candidate_snapshot", sa.Column("score_reason", sa.String(length=500), nullable=True))
    op.add_column("candidate_snapshot", sa.Column("excluded_reason", sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column("candidate_snapshot", "excluded_reason")
    op.drop_column("candidate_snapshot", "score_reason")
    op.drop_column("candidate_snapshot", "component_scores")
    op.drop_column("candidate_snapshot", "strategy_type")
    op.drop_column("candidate_snapshot", "score_type")
