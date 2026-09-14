"""record what a watched upper-limit ticker actually showed, so a no-trade day explains itself

Revision ID: 0034_limit_up_watch_observation
Revises: 0033_limit_up_scan_rejections
Create Date: 2026-09-14
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0034_limit_up_watch_observation"
down_revision: Union[str, None] = "0033_limit_up_scan_rejections"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMNS = (
    # 0 이면 시세가 아예 안 들어왔다는 뜻 — 구독·피드 장애와 정상 미발동을 가른다.
    sa.Column("observed_tick_count", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("observed_low_price", sa.Integer(), nullable=True),
    sa.Column("observed_high_price", sa.Integer(), nullable=True),
    sa.Column("trigger_cross_count", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("max_turnover_krw", sa.BigInteger(), nullable=True),
    sa.Column("max_strength", sa.Float(), nullable=True),
)


def upgrade() -> None:
    """Add the per-session observation counters.

    server_default 를 두는 이유는 기존 행 백필 때문이다. NOT NULL 두 컬럼을 기본값
    없이 붙이면 이미 쌓인 세션 행에서 즉시 실패한다.
    """
    for column in _COLUMNS:
        op.add_column("limit_up_session", column)


def downgrade() -> None:
    """Drop the observation counters."""
    for column in reversed(_COLUMNS):
        op.drop_column("limit_up_session", column.name)
