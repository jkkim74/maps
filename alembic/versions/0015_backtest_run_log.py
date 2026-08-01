"""add backtest_run_log for console run results

SCR-07 콘솔의 최근 실행 목록이 WFA 결과를 원천으로 써서 NET CAGR·거래수가
항상 비어 있었다. 실행 결과를 자체 테이블에 저장해 목록이 실측값을 갖게 한다.

Revision ID: 0015_backtest_run_log
Revises: 0014_regime_korea_weak_guard
Create Date: 2026-08-02

주의: revision ID는 alembic_version.version_num이 varchar(32)라 32자를 넘으면
운영 Postgres에서 UPDATE가 실패한다.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0015_backtest_run_log"
down_revision: Union[str, None] = "0014_regime_korea_weak_guard"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 멱등 보강: startup의 Base.metadata.create_all()이 먼저 만든 경우 건너뛴다.
    bind = op.get_bind()
    if sa.inspect(bind).has_table("backtest_run_log"):
        return
    op.create_table(
        "backtest_run_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(64), nullable=False, unique=True),
        sa.Column("strategy_id", sa.String(64), nullable=False, index=True),
        sa.Column("params_json", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="done"),
        sa.Column("net_cagr", sa.Float(), nullable=True),
        sa.Column("mdd", sa.Float(), nullable=True),
        sa.Column("sharpe", sa.Float(), nullable=True),
        sa.Column("trade_count", sa.Integer(), nullable=True),
        sa.Column("ticker_count", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("backtest_run_log")
