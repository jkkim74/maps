"""add bracket-execution columns to analysis_pick (Part B)

Revision ID: 0007_analysis_pick_bracket
Revises: 0006_analysis_pick
Create Date: 2026-06-25
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007_analysis_pick_bracket"
down_revision: Union[str, None] = "0006_analysis_pick"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW_COLUMNS = ("entry_order_id", "exit_order_id", "last_action_at")


def upgrade() -> None:
    # 멱등 보강: startup의 Base.metadata.create_all()이 컬럼을 이미 만든 경우 건너뛴다.
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns("analysis_pick")}
    with op.batch_alter_table("analysis_pick") as batch:
        if "entry_order_id" not in existing:
            batch.add_column(sa.Column("entry_order_id", sa.String(64), nullable=True))
        if "exit_order_id" not in existing:
            batch.add_column(sa.Column("exit_order_id", sa.String(64), nullable=True))
        if "last_action_at" not in existing:
            batch.add_column(sa.Column("last_action_at", sa.DateTime, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("analysis_pick") as batch:
        for col in _NEW_COLUMNS:
            batch.drop_column(col)
