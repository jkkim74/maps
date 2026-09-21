"""keep every trigger cross, not just the first, so the entry gate can be calibrated

Revision ID: 0035_limit_up_cross_samples
Revises: 0034_limit_up_watch_observation
Create Date: 2026-09-21
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0035_limit_up_cross_samples"
down_revision: Union[str, None] = "0034_limit_up_watch_observation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the per-cross gate reading list.

    nullable 인 이유는 기존 행과 교차가 없던 세션을 구분하기 위해서다. NULL 은
    "이 배포 이전 세션", 빈 리스트는 "재돌파가 한 번도 없었다" 로 읽는다.
    """
    op.add_column(
        "limit_up_session",
        sa.Column("cross_samples", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    """Drop the per-cross gate readings."""
    op.drop_column("limit_up_session", "cross_samples")
