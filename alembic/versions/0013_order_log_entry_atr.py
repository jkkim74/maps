"""add atr14 to order_log

손절가가 진입 후에도 매일 움직였다. 청산·화면이 그날의 ATR 로 손절가를 다시
계산하는데 사이징은 진입 시점 한 번뿐이라, ATR 이 커지면 손절폭만 넓어져
계좌 위험이 예산을 넘어간다(2026-07-31: 089860 0.50% → 0.55%).

진입 시점 ATR 을 매수 주문에 기록해 청산·화면이 재사용한다. 손절가 자체는
저장하지 않는다 — effective_stop_price() 가 유일한 산출 경로여야 하므로
그 **입력**만 고정한다.

Revision ID: 0013_order_log_entry_atr
Revises: 0012_order_log_exit_reason
Create Date: 2026-07-31
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013_order_log_entry_atr"
down_revision: Union[str, None] = "0012_order_log_exit_reason"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 멱등 보강: startup의 Base.metadata.create_all()이 컬럼을 이미 만든 경우 건너뛴다.
    # nullable 컬럼 추가는 SQLite도 plain ADD COLUMN을 지원하므로 batch(테이블 재생성) 불필요.
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns("order_log")}
    if "atr14" not in existing:
        op.add_column("order_log", sa.Column("atr14", sa.Float(), nullable=True))


def downgrade() -> None:
    # DROP COLUMN은 구버전 SQLite 호환을 위해 batch(테이블 재생성) 경로 사용.
    with op.batch_alter_table("order_log") as batch:
        batch.drop_column("atr14")
