"""persist AI candidate score provenance and bounded invocation usage

Revision ID: 0020_ai_scoring
Revises: 0019_bt_run_source
Create Date: 2026-08-07
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0020_ai_scoring"
down_revision: Union[str, None] = "0019_bt_run_source"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CANDIDATE_COLUMNS: tuple[sa.Column, ...] = (
    sa.Column("rule_score", sa.Float(), nullable=True),
    sa.Column("recommendation_score", sa.Float(), nullable=True),
    sa.Column("score_source", sa.String(24), nullable=True),
    sa.Column("ai_scoring_mode", sa.String(16), nullable=True),
    sa.Column("ai_status", sa.String(32), nullable=True),
    sa.Column("ai_confidence", sa.Float(), nullable=True),
    sa.Column("ai_reason_codes", sa.JSON(), nullable=True),
    sa.Column("ai_model_id", sa.String(128), nullable=True),
)


def upgrade() -> None:
    """Add backward-compatible score columns and the durable invocation cache."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {
        column["name"] for column in inspector.get_columns("candidate_snapshot")
    }
    for column in _CANDIDATE_COLUMNS:
        if column.name not in existing_columns:
            op.add_column("candidate_snapshot", column)

    op.execute(
        "UPDATE candidate_snapshot SET rule_score = final_score "
        "WHERE rule_score IS NULL"
    )
    op.execute(
        "UPDATE candidate_snapshot SET recommendation_score = final_score "
        "WHERE recommendation_score IS NULL"
    )
    op.execute(
        "UPDATE candidate_snapshot SET score_source = 'RULE' "
        "WHERE score_source IS NULL"
    )
    op.execute(
        "UPDATE candidate_snapshot SET ai_scoring_mode = 'off' "
        "WHERE ai_scoring_mode IS NULL"
    )

    inspector = sa.inspect(bind)
    if "ai_scoring_invocation" not in inspector.get_table_names():
        op.create_table(
            "ai_scoring_invocation",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("ref_date", sa.Date(), nullable=False),
            sa.Column("ticker", sa.String(16), nullable=False),
            sa.Column("input_hash", sa.String(64), nullable=False),
            sa.Column("model_id", sa.String(128), nullable=False),
            sa.Column("prompt_version", sa.String(32), nullable=False),
            sa.Column("status", sa.String(24), nullable=False),
            sa.Column("score_payload", sa.JSON(), nullable=True),
            sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("error_code", sa.String(64), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.UniqueConstraint(
                "ref_date",
                "ticker",
                "input_hash",
                "model_id",
                "prompt_version",
                name="uq_ai_scoring_invocation_cache_key",
            ),
        )
        op.create_index(
            "ix_ai_scoring_invocation_ref_date",
            "ai_scoring_invocation",
            ["ref_date"],
        )
        op.create_index(
            "ix_ai_scoring_invocation_ticker",
            "ai_scoring_invocation",
            ["ticker"],
        )
        op.create_index(
            "ix_ai_scoring_invocation_status",
            "ai_scoring_invocation",
            ["status"],
        )


def downgrade() -> None:
    """Remove the AI invocation cache and candidate provenance columns."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "ai_scoring_invocation" in inspector.get_table_names():
        op.drop_table("ai_scoring_invocation")

    existing_columns = {
        column["name"] for column in sa.inspect(bind).get_columns("candidate_snapshot")
    }
    with op.batch_alter_table("candidate_snapshot") as batch:
        for column in reversed(_CANDIDATE_COLUMNS):
            if column.name in existing_columns:
                batch.drop_column(column.name)
