"""add korea_weak_guard_applied to market_regime_log

글로벌 8자산 투표가 MIXED를 내도 KOSPI가 5·10주선을 모두 깨고 추세강도/breadth가
약하면 WEAK로 내리는 Korea weak guard(플로어의 대칭)가 추가됐다. 어떤 날의 WEAK가
투표 결과인지 가드 하향인지 감사 로그로 구분해야 한다.

Revision ID: 0014_market_regime_korea_weak_guard
Revises: 0013_order_log_entry_atr
Create Date: 2026-07-30
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0014_market_regime_korea_weak_guard"
down_revision: Union[str, None] = "0013_order_log_entry_atr"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 멱등 보강: startup의 Base.metadata.create_all()이 컬럼을 이미 만든 경우 건너뛴다.
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns("market_regime_log")}
    if "korea_weak_guard_applied" not in existing:
        op.add_column(
            "market_regime_log",
            sa.Column(
                "korea_weak_guard_applied",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )


def downgrade() -> None:
    # DROP COLUMN은 구버전 SQLite 호환을 위해 batch(테이블 재생성) 경로 사용.
    with op.batch_alter_table("market_regime_log") as batch:
        batch.drop_column("korea_weak_guard_applied")
