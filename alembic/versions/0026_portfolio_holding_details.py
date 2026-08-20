"""store decision-time portfolio holding details

Revision ID: 0026_holding_details
Revises: 0025_pick_ai_reco
Create Date: 2026-08-20
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0026_holding_details"
down_revision: Union[str, None] = "0025_pick_ai_reco"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "portfolio_snapshot",
        sa.Column("holding_details", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("portfolio_snapshot", "holding_details")
