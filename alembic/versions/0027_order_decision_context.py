"""store immutable order decision context

Revision ID: 0027_order_decision_context
Revises: 0026_holding_details
Create Date: 2026-08-25
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0027_order_decision_context"
down_revision: Union[str, None] = "0026_holding_details"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "order_log",
        sa.Column("decision_context", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("order_log", "decision_context")
