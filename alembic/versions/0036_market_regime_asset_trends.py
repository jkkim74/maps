"""store per-asset weekly trends on the regime log so the market screen can read it

Revision ID: 0036_market_regime_asset_trends
Revises: 0035_limit_up_cross_samples
Create Date: 2026-09-22
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0036_market_regime_asset_trends"
down_revision: Union[str, None] = "0035_limit_up_cross_samples"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the asset trend list.

    /api/v1/market 이 요청마다 KRX·yfinance 시세 10건과 시장 내부지표를 실시간으로
    계산해 6~28초 걸렸다(2026-09-22 실측). 화면이 스케줄러 판정 이력을 읽으려면
    이력에 없던 자산별 방향 목록이 필요하다. NULL 은 "이 배포 이전 행" 이라 화면이
    실시간 계산으로 폴백한다.
    """
    op.add_column(
        "market_regime_log",
        sa.Column("asset_trends", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    """Drop the asset trend list."""
    op.drop_column("market_regime_log", "asset_trends")
