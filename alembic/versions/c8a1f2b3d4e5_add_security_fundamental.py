"""add security_fundamental table

Revision ID: c8a1f2b3d4e5
Revises: b7c9a1d4e8f2
Create Date: 2026-06-20
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "c8a1f2b3d4e5"
down_revision: Union[str, None] = "b7c9a1d4e8f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "security_fundamental"


def _existing_indexes(table: str) -> set[str]:
    inspector = inspect(op.get_bind())
    if not inspector.has_table(table):
        return set()
    return {ix["name"] for ix in inspector.get_indexes(table)}


def upgrade() -> None:
    # 멱등 보강: startup의 Base.metadata.create_all()이 먼저 테이블을 만든 경우에도
    # "already exists"로 실패하지 않도록 존재 여부를 확인한다.
    inspector = inspect(op.get_bind())
    if not inspector.has_table(_TABLE):
        op.create_table(
            _TABLE,
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("ticker", sa.String(16), nullable=False),
            sa.Column("date", sa.Date, nullable=False),
            sa.Column("per", sa.Float, nullable=True),
            sa.Column("pbr", sa.Float, nullable=True),
            sa.Column("eps", sa.Float, nullable=True),
            sa.Column("bps", sa.Float, nullable=True),
            sa.Column("div", sa.Float, nullable=True),
            sa.Column("dps", sa.Float, nullable=True),
            sa.Column("source", sa.String(32), nullable=False, server_default="pykrx"),
            sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("ticker", "date", name="uq_security_fundamental_ticker_date"),
        )

    existing = _existing_indexes(_TABLE)
    if "ix_security_fundamental_ticker" not in existing:
        op.create_index("ix_security_fundamental_ticker", _TABLE, ["ticker"])
    if "ix_security_fundamental_date" not in existing:
        op.create_index("ix_security_fundamental_date", _TABLE, ["date"])


def downgrade() -> None:
    inspector = inspect(op.get_bind())
    if not inspector.has_table(_TABLE):
        return
    existing = _existing_indexes(_TABLE)
    if "ix_security_fundamental_date" in existing:
        op.drop_index("ix_security_fundamental_date", table_name=_TABLE)
    if "ix_security_fundamental_ticker" in existing:
        op.drop_index("ix_security_fundamental_ticker", table_name=_TABLE)
    op.drop_table(_TABLE)
