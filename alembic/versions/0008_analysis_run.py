"""add analysis_run audit table (/analyze 실행 이력, 0종목 포함)

Revision ID: 0008_analysis_run
Revises: 0007_analysis_pick_bracket
Create Date: 2026-06-27
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008_analysis_run"
down_revision: Union[str, None] = "0007_analysis_pick_bracket"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 멱등 보강: startup의 Base.metadata.create_all()이 먼저 테이블을 만든 경우 건너뛴다.
    bind = op.get_bind()
    if "analysis_run" in sa.inspect(bind).get_table_names():
        return

    op.create_table(
        "analysis_run",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("ref_date", sa.Date, nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="completed"),
        sa.Column("source", sa.String(16), nullable=False, server_default="analyze"),
        sa.Column("regime", sa.String(16), nullable=True),
        sa.Column("strategy_context", sa.String(128), nullable=True),
        sa.Column("picks_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("candidates_count", sa.Integer, nullable=True),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
    )
    op.create_index("ix_analysis_run_ref_date", "analysis_run", ["ref_date"])


def downgrade() -> None:
    op.drop_index("ix_analysis_run_ref_date", table_name="analysis_run")
    op.drop_table("analysis_run")
