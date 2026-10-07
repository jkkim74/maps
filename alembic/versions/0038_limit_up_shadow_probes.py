"""record what each trigger cross would have become, so gates can be judged by outcome

Revision ID: 0038_limit_up_shadow_probes
Revises: 0037_close_report_evidence
Create Date: 2026-10-02
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0038_limit_up_shadow_probes"
down_revision: Union[str, None] = "0037_close_report_evidence"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the per-cross shadow outcome list.

    nullable 인 이유는 cross_samples 와 같다. NULL 은 "이 배포 이전 세션", 빈 리스트는
    "끝까지 간 프로브가 없었다" 로 읽는다.
    """
    op.add_column(
        "limit_up_session",
        sa.Column("shadow_probes", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    """Drop the shadow outcomes."""
    op.drop_column("limit_up_session", "shadow_probes")
