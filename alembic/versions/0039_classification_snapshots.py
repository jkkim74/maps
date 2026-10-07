"""Immutable classification publications and separate collection quality."""
from alembic import op
import sqlalchemy as sa

revision = "0039_classification_snapshots"
down_revision = "0038_execution_safety"
branch_labels = None
depends_on = None


def upgrade():
    """Add snapshot storage without backfilling historical classifications."""
    op.add_column("collection_log", sa.Column("classification_quality", sa.JSON(), nullable=True))
    op.create_table("classification_run",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("ref_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("expected_tickers", sa.JSON(), nullable=False),
        sa.Column("catalog", sa.JSON(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("notified_at", sa.DateTime(), nullable=True))
    for column in ("kind", "ref_date", "status"):
        op.create_index(f"ix_classification_run_{column}", "classification_run", [column])
    op.create_table("classification_member",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("classification_run.id"), nullable=False),
        sa.Column("ticker", sa.String(16), nullable=False),
        sa.Column("code", sa.String(128), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.UniqueConstraint("run_id", "ticker", "code", name="uq_classification_member"))
    for column in ("run_id", "ticker"):
        op.create_index(f"ix_classification_member_{column}", "classification_member", [column])


def downgrade():
    """Remove only the additive classification schema."""
    op.drop_table("classification_member")
    op.drop_table("classification_run")
    op.drop_column("collection_log", "classification_quality")
