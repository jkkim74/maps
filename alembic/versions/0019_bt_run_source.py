"""add source to backtest_run_log

자동 검증 백테스트와 콘솔 수동 실행을 같은 최근 실행 목록에서 구분한다.

Revision ID: 0019_bt_run_source
Revises: 0018_candidate_entry_signal
Create Date: 2026-08-05
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0019_bt_run_source"
down_revision: Union[str, None] = "0018_candidate_entry_signal"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """기존 콘솔 실행 행을 manual로 유지하며 출처 컬럼을 추가한다."""
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns("backtest_run_log")}
    if "source" not in existing:
        op.add_column(
            "backtest_run_log",
            sa.Column("source", sa.String(24), nullable=False, server_default="manual"),
        )
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("backtest_run_log")}
    if "ix_backtest_run_log_source" not in indexes:
        op.create_index("ix_backtest_run_log_source", "backtest_run_log", ["source"])


def downgrade() -> None:
    """출처 인덱스와 컬럼을 제거한다."""
    with op.batch_alter_table("backtest_run_log") as batch:
        batch.drop_index("ix_backtest_run_log_source")
        batch.drop_column("source")
