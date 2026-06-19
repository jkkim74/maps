"""add sector to security_metadata

Revision ID: 1b89a330a0f6
Revises: efca8676041a
Create Date: 2026-06-19 14:03:12.301971

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '1b89a330a0f6'
down_revision: Union[str, None] = 'efca8676041a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('security_metadata', sa.Column('sector', sa.String(length=100), nullable=True))


def downgrade() -> None:
    op.drop_column('security_metadata', 'sector')
