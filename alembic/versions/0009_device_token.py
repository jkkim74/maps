"""add device_token table (FCM 네이티브 푸시 등록, Phase 4)

Revision ID: 0009_device_token
Revises: 0008_analysis_run
Create Date: 2026-07-01
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009_device_token"
down_revision: Union[str, None] = "0008_analysis_run"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 멱등 보강: startup의 Base.metadata.create_all()이 먼저 테이블을 만든 경우 건너뛴다.
    bind = op.get_bind()
    if "device_token" in sa.inspect(bind).get_table_names():
        return

    op.create_table(
        "device_token",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("token", sa.String(512), nullable=False),
        sa.Column("platform", sa.String(16), nullable=False, server_default="android"),
        sa.Column("username", sa.String(128), nullable=True),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("last_seen_at", sa.DateTime, nullable=True),
    )
    op.create_index("ix_device_token_token", "device_token", ["token"], unique=True)
    op.create_index("ix_device_token_active", "device_token", ["active"])


def downgrade() -> None:
    op.drop_index("ix_device_token_active", table_name="device_token")
    op.drop_index("ix_device_token_token", table_name="device_token")
    op.drop_table("device_token")
