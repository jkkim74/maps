"""Nullable research evidence and immutable first-seen DART snapshots."""
from alembic import op
import sqlalchemy as sa

revision = "0037_close_report_evidence"
down_revision = "0036_market_regime_asset_trends"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("collection_log", sa.Column("metadata_quality", sa.JSON(), nullable=True))
    for name, kind in (("score_version", sa.String(64)), ("score_scope", sa.String(16)), ("score_evidence", sa.JSON())):
        op.add_column("candidate_snapshot", sa.Column(name, kind, nullable=True))
    op.create_table("dart_financial_snapshot",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ticker", sa.String(16), nullable=False),
        sa.Column("receipt", sa.String(32), nullable=False),
        sa.Column("basis", sa.String(3), nullable=False),
        sa.Column("raw_hash", sa.String(64), nullable=False),
        sa.Column("raw_response", sa.JSON(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("publication_date", sa.Date(), nullable=False),
        sa.Column("first_collected_at", sa.DateTime(), nullable=False),
        sa.Column("available_date", sa.Date(), nullable=False),
        sa.Column("currency", sa.String(16), nullable=False),
        *[sa.Column(name, sa.Numeric(30, 2), nullable=False) for name in
          ("revenue", "prior_revenue", "operating_profit", "prior_operating_profit")],
        sa.UniqueConstraint("ticker", "receipt", "basis", "raw_hash", name="uq_dart_snapshot"))
    op.create_table("dart_filing_receipt",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ticker", sa.String(16), nullable=False),
        sa.Column("receipt", sa.String(32), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("publication_date", sa.Date(), nullable=False),
        sa.Column("first_collected_at", sa.DateTime(), nullable=False),
        sa.Column("available_date", sa.Date(), nullable=False),
        sa.UniqueConstraint("ticker", "receipt", name="uq_dart_receipt"))
    for table in ("dart_financial_snapshot", "dart_filing_receipt"):
        for field in ("ticker", "available_date"):
            op.create_index(f"ix_{table}_{field}", table, [field])
    op.create_table("dart_collection_state",
        sa.Column("ticker", sa.String(16), primary_key=True),
        sa.Column("checked_at", sa.DateTime(), nullable=True),
        sa.Column("retry_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("error", sa.String(128), nullable=True),
        sa.Column("receipts", sa.JSON(), nullable=True))


def downgrade():
    for table in ("dart_collection_state", "dart_filing_receipt", "dart_financial_snapshot"):
        op.drop_table(table)
    for name in ("score_evidence", "score_scope", "score_version"):
        op.drop_column("candidate_snapshot", name)
    op.drop_column("collection_log", "metadata_quality")
