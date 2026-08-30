"""persist upper-limit execution provenance and exit quantities

Revision ID: 0032_limit_up_ledger
Revises: 0031_limit_up_guard
Create Date: 2026-08-30
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0032_limit_up_ledger"
down_revision: Union[str, None] = "0031_limit_up_guard"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add durable provenance and a per-KST-date cumulative exit ledger."""
    op.add_column(
        "limit_up_session",
        sa.Column(
            "execution_mode",
            sa.String(length=16),
            nullable=False,
            server_default="unknown",
        ),
    )
    op.add_column(
        "limit_up_session",
        sa.Column("exit_quantity_by_date", sa.JSON(), nullable=True),
    )
    op.execute(sa.text(
        "UPDATE limit_up_session SET execution_mode = 'automatic' "
        "WHERE EXISTS (SELECT 1 FROM limit_up_order_leg leg "
        "WHERE leg.session_id = limit_up_session.id "
        "AND leg.broker_order_id IS NOT NULL)"
    ))
    op.execute(sa.text(
        "UPDATE limit_up_session SET execution_mode = 'recommend_only' "
        "WHERE execution_mode = 'unknown' "
        "AND (exit_order_ids IS NULL OR exit_order_ids = '') "
        "AND EXISTS (SELECT 1 FROM limit_up_order_leg leg "
        "WHERE leg.session_id = limit_up_session.id) "
        "AND NOT EXISTS (SELECT 1 FROM limit_up_order_leg leg "
        "WHERE leg.session_id = limit_up_session.id "
        "AND leg.status NOT IN ('recommended', 'simulated_filled'))"
    ))


def downgrade() -> None:
    """Remove the V1-only provenance and exit ledger columns."""
    op.drop_column("limit_up_session", "exit_quantity_by_date")
    op.drop_column("limit_up_session", "execution_mode")
