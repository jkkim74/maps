"""add entry_signal to candidate_snapshot

후보 생성이 전략 신호를 보지 않아 유니버스 전량(하루 약 1만 행)이 "후보"로 쌓였다.
신호를 후보 생성 시점으로 끌어오면서, 어떤 행이 실제 진입 신호를 동반했는지 남긴다.
nullable 인 이유: 구 데이터와 상위 N 관측행은 신호를 계산하지 않은 상태라
False(신호 없음)와 구분해야 한다.

Revision ID: 0018_candidate_entry_signal
Revises: 0017_bt_run_period_verdict
Create Date: 2026-08-03

주의: revision ID는 alembic_version.version_num이 varchar(32)라 32자를 넘으면
운영 Postgres에서 UPDATE가 실패한다 (2026-08-02 배포 중 실제 발생, 35자였음).
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0018_candidate_entry_signal"
down_revision: Union[str, None] = "0017_bt_run_period_verdict"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 멱등 보강: startup의 Base.metadata.create_all()이 컬럼을 이미 만든 경우 건너뛴다.
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns("candidate_snapshot")}
    if "entry_signal" not in existing:
        op.add_column(
            "candidate_snapshot",
            sa.Column("entry_signal", sa.Boolean(), nullable=True),
        )


def downgrade() -> None:
    # DROP COLUMN은 구버전 SQLite 호환을 위해 batch(테이블 재생성) 경로 사용.
    with op.batch_alter_table("candidate_snapshot") as batch:
        batch.drop_column("entry_signal")
