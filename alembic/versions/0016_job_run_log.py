"""add job_run_log for scheduler run history

SCR-21 배치 모니터. 잡 실행 결과가 인메모리 `_last_runs`에만 있어 재시작(배포)
후 소실되고, 실패는 어떤 테이블에도 남지 않았다. 실행 이력을 자체 테이블에
저장해 "오늘 배치가 정상적으로 돌았는가"를 화면에서 확인할 수 있게 한다.

Revision ID: 0016_job_run_log
Revises: 0015_backtest_run_log
Create Date: 2026-08-02

주의: revision ID는 alembic_version.version_num이 varchar(32)라 32자를 넘으면
운영 Postgres에서 UPDATE가 실패한다.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0016_job_run_log"
down_revision: Union[str, None] = "0015_backtest_run_log"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 멱등 보강: startup의 Base.metadata.create_all()이 먼저 만든 경우 건너뛴다.
    bind = op.get_bind()
    if sa.inspect(bind).has_table("job_run_log"):
        return
    op.create_table(
        "job_run_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(32), nullable=False, index=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("ref_date", sa.Date(), nullable=False, index=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("details_json", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("job_run_log")
