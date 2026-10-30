"""Normalized daily source checkpoints and dependency claims; no backfill."""
from alembic import op
import sqlalchemy as sa
revision = "0045_ohlcv_normalization"
down_revision = "0044_runtime_execution_policy"
branch_labels = None
depends_on = None


def upgrade():
    """Create empty audit tables without modifying historical prices."""
    op.create_table("ohlcv_collection_run",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ref_date", sa.Date(), nullable=False, unique=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("owner", sa.String(64)), sa.Column("lease_until", sa.DateTime()),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime()), sa.Column("published_at", sa.DateTime()),
        sa.Column("expected", sa.JSON(), nullable=False),
        sa.Column("requests", sa.Integer(), nullable=False),
        sa.Column("quality", sa.JSON(), nullable=False), sa.Column("snapshot", sa.JSON()))
    op.create_table("ohlcv_collection_item",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("ohlcv_collection_run.id"), nullable=False),
        sa.Column("ticker", sa.String(16), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("run_id", "ticker", name="uq_ohlcv_run_ticker"))
    op.create_table("daily_pipeline_stage",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ref_date", sa.Date(), nullable=False),
        sa.Column("name", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("owner", sa.String(64)), sa.Column("lease_until", sa.DateTime()),
        sa.Column("started_at", sa.DateTime()), sa.Column("finished_at", sa.DateTime()),
        sa.Column("details", sa.JSON()),
        sa.UniqueConstraint("ref_date", "name", name="uq_daily_pipeline_stage"))


def downgrade():
    """Never erase populated observations or dependency records."""
    for table in ("daily_pipeline_stage", "ohlcv_collection_item", "ohlcv_collection_run"):
        if op.get_bind().execute(sa.text("SELECT count(*) FROM " + table)).scalar():
            raise RuntimeError("Preserve populated OHLCV audit tables on rollback")
    for table in ("daily_pipeline_stage", "ohlcv_collection_item", "ohlcv_collection_run"):
        op.drop_table(table)
