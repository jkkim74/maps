"""Append-only paper actual-cost evidence and settlement receipts; no backfill."""
from alembic import op
import sqlalchemy as sa

revision = "0042_paper_cost_settlement"
down_revision = "0041_fujimoto_trading"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create empty account-scoped actual-cost history."""
    op.create_table("paper_cost_evidence",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_key", sa.String(128), nullable=False),
        sa.Column("environment", sa.String(16), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("ref_date", sa.Date(), nullable=False),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.Column("amount", sa.Numeric(24, 4), nullable=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("supersedes_id", sa.Integer(), nullable=True),
        sa.UniqueConstraint("account_key", "fingerprint", name="uq_paper_cost_evidence"))
    op.create_index("ix_paper_cost_evidence_account_key", "paper_cost_evidence", ["account_key"])
    op.create_table("paper_cost_settlement",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_key", sa.String(128), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.UniqueConstraint("account_key", "idempotency_key", name="uq_paper_cost_settlement"))
    op.create_index("ix_paper_cost_settlement_account_key", "paper_cost_settlement", ["account_key"])


def downgrade() -> None:
    """Remove empty additive objects; populated audit history must be retained."""
    connection = op.get_bind()
    for table in ("paper_cost_settlement", "paper_cost_evidence"):
        if connection.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar_one():
            raise RuntimeError("paper_cost_history_not_empty")
    op.drop_table("paper_cost_settlement")
    op.drop_table("paper_cost_evidence")
