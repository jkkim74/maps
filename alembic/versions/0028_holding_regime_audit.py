"""add read-only holding regime audit

Revision ID: 0028_holding_regime_audit
Revises: 0027_order_decision_context
Create Date: 2026-08-26
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0028_holding_regime_audit"
down_revision: Union[str, None] = "0027_order_decision_context"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "holding_regime_audit",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("ref_date", sa.Date(), nullable=False),
        sa.Column("position_key", sa.String(64), nullable=False),
        sa.Column("ticker", sa.String(16), nullable=False),
        sa.Column("strategy_id", sa.String(64), nullable=False),
        sa.Column("entry_regime", sa.String(16), nullable=True),
        sa.Column("current_regime", sa.String(16), nullable=True),
        sa.Column("weekly_trend", sa.String(8), nullable=True),
        sa.Column("vol_regime", sa.String(8), nullable=True),
        sa.Column("action", sa.String(8), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("confirmed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("mode", sa.String(16), nullable=False, server_default="shadow"),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "ref_date",
            "position_key",
            name="uq_holding_regime_audit_ref_position",
        ),
    )
    op.create_index(
        "ix_holding_regime_audit_ref_date",
        "holding_regime_audit",
        ["ref_date"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_holding_regime_audit_ref_date",
        table_name="holding_regime_audit",
    )
    op.drop_table("holding_regime_audit")
