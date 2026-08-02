"""add period/universe/verdict columns to backtest_run_log

SCR-07 콘솔이 기간 지정·대상(유니버스) 선택·1차 성공 판정을 지원하면서
실행 조건과 판정 결과를 저장할 컬럼이 필요해졌다. 전부 nullable — 구 실행
행은 "전체 기간, 판정 없음"으로 그대로 유효하다.

Revision ID: 0017_bt_run_period_verdict
Revises: 0016_job_run_log
Create Date: 2026-08-02

주의: revision ID는 alembic_version.version_num이 varchar(32)라 32자를 넘으면
운영 Postgres에서 UPDATE가 실패한다.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0017_bt_run_period_verdict"
down_revision: Union[str, None] = "0016_job_run_log"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMNS: list[tuple[str, sa.types.TypeEngine]] = [
    ("start_date", sa.Date()),
    ("end_date", sa.Date()),
    ("mode", sa.String(16)),
    ("universe", sa.String(32)),
    ("verdict", sa.String(8)),
    ("verdict_json", sa.Text()),
    ("stats_json", sa.Text()),
]


def upgrade() -> None:
    # 멱등 보강: startup의 Base.metadata.create_all()이 먼저 만든 경우 건너뛴다.
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns("backtest_run_log")}
    for name, column_type in _COLUMNS:
        if name not in existing:
            op.add_column("backtest_run_log", sa.Column(name, column_type, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("backtest_run_log") as batch:
        for name, _ in reversed(_COLUMNS):
            batch.drop_column(name)
