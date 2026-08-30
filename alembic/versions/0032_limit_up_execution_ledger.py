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
    # 백필 없음 — alembic/CLAUDE.md 규칙. 운영 limit_up_session 은 이 시점 0행이었다.


def downgrade() -> None:
    """Remove the V1-only provenance and exit ledger columns."""
    op.drop_column("limit_up_session", "exit_quantity_by_date")
    op.drop_column("limit_up_session", "execution_mode")
