"""record the AI recommendation on each armed analysis pick

Revision ID: 0025_pick_ai_reco
Revises: 0024_app_user
Create Date: 2026-08-18
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0025_pick_ai_reco"
down_revision: Union[str, None] = "0024_app_user"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the nullable AI recommendation column.

    기존 행은 NULL 로 남긴다 — backfill 하지 않는다. NULL 이 곧 '이 기록 이전'
    이라는 뜻이고, 지어낸 값으로 채우면 감사 기록이 거짓이 된다.
    """
    op.add_column(
        "analysis_pick",
        sa.Column("ai_recommendation", sa.String(length=16), nullable=True),
    )


def downgrade() -> None:
    """Drop the recommendation column."""
    op.drop_column("analysis_pick", "ai_recommendation")
