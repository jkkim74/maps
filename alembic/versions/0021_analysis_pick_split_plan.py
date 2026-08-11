"""add split strategy-trade plans to analysis_pick

Revision ID: 0021_analysis_pick_split_plan
Revises: 0020_ai_scoring
Create Date: 2026-08-11
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0021_analysis_pick_split_plan"
down_revision: Union[str, None] = "0020_ai_scoring"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {column["name"] for column in inspector.get_columns("analysis_pick")}
    columns = (
        sa.Column("trade_mode", sa.String(16), nullable=False, server_default="single"),
        sa.Column("total_budget", sa.Float(), nullable=True),
        sa.Column("entries_cancelled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("exit_pending_reason", sa.String(16), nullable=True),
    )
    for column in columns:
        if column.name not in existing:
            op.add_column("analysis_pick", column)

    indexes = {index["name"] for index in inspector.get_indexes("analysis_pick")}
    if "uq_analysis_pick_active_ticker" not in indexes:
        duplicates = bind.execute(sa.text(
            "SELECT ticker FROM analysis_pick "
            "WHERE state IN ('ARMED', 'BOUGHT') "
            "GROUP BY ticker HAVING COUNT(*) > 1 ORDER BY ticker"
        )).scalars().all()
        if duplicates:
            tickers = ", ".join(str(ticker) for ticker in duplicates)
            raise RuntimeError(
                "Cannot create uq_analysis_pick_active_ticker; "
                f"resolve duplicate active tickers first: {tickers}"
            )
        active_states = sa.text("state IN ('ARMED', 'BOUGHT')")
        op.create_index(
            "uq_analysis_pick_active_ticker",
            "analysis_pick",
            ["ticker"],
            unique=True,
            sqlite_where=active_states,
            postgresql_where=active_states,
        )

    if "analysis_pick_leg" not in inspector.get_table_names():
        op.create_table(
            "analysis_pick_leg",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "pick_id", sa.Integer(),
                sa.ForeignKey("analysis_pick.id", ondelete="CASCADE"), nullable=False,
            ),
            sa.Column("sequence", sa.Integer(), nullable=False),
            sa.Column("entry_price", sa.Float(), nullable=False),
            sa.Column("weight_pct", sa.Integer(), nullable=False),
            sa.Column("planned_qty", sa.Integer(), nullable=False),
            sa.Column("filled_qty", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("fill_price", sa.Float(), nullable=True),
            sa.Column("current_order_fill_qty", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("order_id", sa.String(64), nullable=True),
            sa.Column("status", sa.String(16), nullable=False, server_default="PENDING"),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("pick_id", "sequence", name="uq_analysis_pick_leg_sequence"),
        )
        op.create_index("ix_analysis_pick_leg_pick_id", "analysis_pick_leg", ["pick_id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "analysis_pick_leg" in inspector.get_table_names():
        op.drop_table("analysis_pick_leg")

    indexes = {index["name"] for index in inspector.get_indexes("analysis_pick")}
    if "uq_analysis_pick_active_ticker" in indexes:
        op.drop_index("uq_analysis_pick_active_ticker", table_name="analysis_pick")

    existing = {column["name"] for column in sa.inspect(bind).get_columns("analysis_pick")}
    with op.batch_alter_table("analysis_pick") as batch:
        for name in ("exit_pending_reason", "entries_cancelled", "total_budget", "trade_mode"):
            if name in existing:
                batch.drop_column(name)
