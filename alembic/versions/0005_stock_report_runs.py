"""add stock_report_runs table

Revision ID: 0005_stock_report_runs
Revises: 0004_portfolio_snapshot
Create Date: 2026-05-30
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_stock_report_runs"
down_revision: Union[str, None] = "0004_portfolio_snapshot"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "stock_report_runs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("report_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),
        sa.Column("trade_date", sa.String(16), nullable=True),
        sa.Column("html_content", sa.Text, nullable=True),
        sa.Column("meta_json", sa.Text, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime, nullable=True),
    )
    op.create_index("ix_stock_report_runs_report_type", "stock_report_runs", ["report_type"])
    op.create_index("ix_stock_report_runs_status", "stock_report_runs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_stock_report_runs_status", table_name="stock_report_runs")
    op.drop_index("ix_stock_report_runs_report_type", table_name="stock_report_runs")
    op.drop_table("stock_report_runs")
