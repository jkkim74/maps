"""add upper-limit V1 session audit tables

Revision ID: 0029_limit_up_v1
Revises: 0028_holding_regime_audit
Create Date: 2026-08-28
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0029_limit_up_v1"
down_revision: Union[str, None] = "0028_holding_regime_audit"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the V1 session, fixed-leg, and event audit tables."""
    op.create_table(
        "limit_up_session",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("ref_date", sa.Date(), nullable=False),
        sa.Column("ticker", sa.String(16), nullable=False),
        sa.Column("market", sa.String(16), nullable=False),
        sa.Column("state", sa.String(32), nullable=False, server_default="watching"),
        sa.Column("state_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("upper_limit_price", sa.Integer(), nullable=False),
        sa.Column("trigger_price", sa.Integer(), nullable=False),
        sa.Column("total_listed_shares", sa.BigInteger(), nullable=False),
        sa.Column("trigger_turnover_krw", sa.BigInteger(), nullable=True),
        sa.Column("trigger_strength", sa.Float(), nullable=True),
        sa.Column("kosdaq_drawdown", sa.Float(), nullable=True),
        sa.Column("trigger_at", sa.DateTime(), nullable=True),
        sa.Column("net_fired_at", sa.DateTime(), nullable=True),
        sa.Column("first_fill_at", sa.DateTime(), nullable=True),
        sa.Column("locked_at", sa.DateTime(), nullable=True),
        sa.Column("eod_decision", sa.String(16), nullable=True),
        sa.Column("end_reason", sa.String(64), nullable=True),
        sa.Column("exit_order_ids", sa.String(512), nullable=True),
        sa.Column("realized_pnl", sa.Float(), nullable=True),
        sa.Column("after_hours_volume", sa.BigInteger(), nullable=True),
        sa.Column("pattern_failure_counted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("ref_date", "ticker", name="uq_limit_up_session_day_ticker"),
    )
    op.create_index("ix_limit_up_session_ref_date", "limit_up_session", ["ref_date"])
    op.create_index("ix_limit_up_session_ticker", "limit_up_session", ["ticker"])

    op.create_table(
        "limit_up_order_leg",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("limit_up_session.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(1), nullable=False),
        sa.Column("broker_order_id", sa.String(128), nullable=True),
        sa.Column("price", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("filled_quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("avg_fill_price", sa.Float(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="created"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("session_id", "name", name="uq_limit_up_order_leg_session_name"),
    )
    op.create_index("ix_limit_up_order_leg_session_id", "limit_up_order_leg", ["session_id"])

    op.create_table(
        "limit_up_event",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("limit_up_session.id", ondelete="CASCADE"), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("leg", sa.String(1), nullable=True),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("idempotency_key", name="uq_limit_up_event_idempotency_key"),
    )
    op.create_index("ix_limit_up_event_session_id", "limit_up_event", ["session_id"])

    op.create_table(
        "limit_up_tape",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("limit_up_session.id", ondelete="CASCADE"), nullable=False),
        sa.Column("transition", sa.String(32), nullable=False),
        sa.Column("started_at_monotonic", sa.Float(), nullable=True),
        sa.Column("ended_at_monotonic", sa.Float(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_limit_up_tape_session_id", "limit_up_tape", ["session_id"])


def downgrade() -> None:
    """Drop upper-limit V1 audit tables in dependency order."""
    op.drop_index("ix_limit_up_tape_session_id", table_name="limit_up_tape")
    op.drop_table("limit_up_tape")
    op.drop_index("ix_limit_up_event_session_id", table_name="limit_up_event")
    op.drop_table("limit_up_event")
    op.drop_index("ix_limit_up_order_leg_session_id", table_name="limit_up_order_leg")
    op.drop_table("limit_up_order_leg")
    op.drop_index("ix_limit_up_session_ticker", table_name="limit_up_session")
    op.drop_index("ix_limit_up_session_ref_date", table_name="limit_up_session")
    op.drop_table("limit_up_session")
