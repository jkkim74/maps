"""add app_user accounts and per-record ownership

Revision ID: 0024_app_user
Revises: 0023_score_readiness_feeds
Create Date: 2026-08-12
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0024_app_user"
down_revision: Union[str, None] = "0023_score_readiness_feeds"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the account table and nullable owner columns.

    기존 행은 owner_user_id 가 NULL 로 남아 운영자(관리자)에게만 보인다.
    backfill 하지 않는다 — NULL 이 곧 '개인화 이전 운영자 데이터' 라는 뜻이다.
    """
    op.create_table(
        "app_user",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("display_name", sa.String(128), nullable=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(16), nullable=False, server_default="user"),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("plan", sa.String(16), nullable=False, server_default="free"),
        sa.Column("preferences", sa.JSON(), nullable=True),
        sa.Column("daily_analysis_limit", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("username", name="uq_app_user_username"),
    )
    op.create_index("ix_app_user_username", "app_user", ["username"])

    op.add_column(
        "stock_analysis_history", sa.Column("owner_user_id", sa.Integer(), nullable=True)
    )
    op.create_index(
        "ix_stock_analysis_history_owner_user_id",
        "stock_analysis_history",
        ["owner_user_id"],
    )
    op.add_column("analysis_pick", sa.Column("owner_user_id", sa.Integer(), nullable=True))
    op.create_index("ix_analysis_pick_owner_user_id", "analysis_pick", ["owner_user_id"])


def downgrade() -> None:
    """Drop ownership columns and the account table."""
    op.drop_index("ix_analysis_pick_owner_user_id", table_name="analysis_pick")
    op.drop_column("analysis_pick", "owner_user_id")
    op.drop_index(
        "ix_stock_analysis_history_owner_user_id", table_name="stock_analysis_history"
    )
    op.drop_column("stock_analysis_history", "owner_user_id")
    op.drop_index("ix_app_user_username", table_name="app_user")
    op.drop_table("app_user")
