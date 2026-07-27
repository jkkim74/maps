"""add exit_reason to order_log

매도 주문의 청산 사유(stop_loss/take_profit/signal/bracket)가 감사 로그에 남지
않고 journald에만 존재했다. 왜 팔았는지는 주문 자체만큼 중요한 기록이다.

Revision ID: 0012_order_log_exit_reason
Revises: 0011_analysis_pick_exit_reason
Create Date: 2026-07-27
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012_order_log_exit_reason"
down_revision: Union[str, None] = "0011_analysis_pick_exit_reason"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 멱등 보강: startup의 Base.metadata.create_all()이 컬럼을 이미 만든 경우 건너뛴다.
    # nullable 컬럼 추가는 SQLite도 plain ADD COLUMN을 지원하므로 batch(테이블 재생성) 불필요.
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns("order_log")}
    if "exit_reason" not in existing:
        op.add_column("order_log", sa.Column("exit_reason", sa.String(16), nullable=True))


def downgrade() -> None:
    # DROP COLUMN은 구버전 SQLite 호환을 위해 batch(테이블 재생성) 경로 사용.
    with op.batch_alter_table("order_log") as batch:
        batch.drop_column("exit_reason")
