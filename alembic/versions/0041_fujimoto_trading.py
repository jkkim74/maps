"""Durable Fujimoto research evidence and mode-owned staged cycles; no backfill."""
from alembic import op
import sqlalchemy as sa

revision = "0041_fujimoto_trading"
down_revision = "0040_merge_classification_shadow"
branch_labels = None
depends_on = None


def upgrade():
    """Create additive tables; never adopt existing account positions."""
    op.create_table("fujimoto_config",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_key", sa.String(128), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=True),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("budget", sa.Numeric(24, 6), nullable=False),
        sa.Column("deposit", sa.Numeric(24, 6), nullable=False),
        sa.Column("settings", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint('account_key', 'mode', 'version', name="uq_fujimoto_config"))
    op.create_index("ix_fujimoto_config_account_key", "fujimoto_config", ['account_key'])
    op.create_table("fujimoto_evidence",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("ticker", sa.String(16), nullable=False),
        sa.Column("account_key", sa.String(128), nullable=True),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.Column("available_at", sa.DateTime(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False))
    op.create_index("ix_fujimoto_evidence_available_at", "fujimoto_evidence", ['available_at'])
    op.create_index("ix_fujimoto_evidence_kind", "fujimoto_evidence", ['kind'])
    op.create_index("ix_fujimoto_evidence_observed_at", "fujimoto_evidence", ['observed_at'])
    op.create_index("ix_fujimoto_evidence_ticker", "fujimoto_evidence", ['ticker'])
    op.create_table("fujimoto_cycle",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("config_id", sa.Integer(), sa.ForeignKey("fujimoto_config.id"), nullable=False),
        sa.Column("account_key", sa.String(128), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("ticker", sa.String(16), nullable=False),
        sa.Column("budget", sa.Numeric(24, 6), nullable=False),
        sa.Column("state", sa.JSON(), nullable=False),
        sa.Column("cost_basis", sa.Numeric(24, 6), nullable=False),
        sa.Column("realized_pnl", sa.Numeric(24, 6), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False))
    op.create_index("ix_fujimoto_cycle_account_key", "fujimoto_cycle", ['account_key'])
    op.create_index("ix_fujimoto_cycle_ticker", "fujimoto_cycle", ['ticker'])
    op.create_table("fujimoto_order",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cycle_id", sa.Integer(), sa.ForeignKey("fujimoto_cycle.id"), nullable=False),
        sa.Column("evidence_id", sa.Integer(), sa.ForeignKey("fujimoto_evidence.id"), nullable=False),
        sa.Column("account_key", sa.String(128), nullable=False),
        sa.Column("intent_id", sa.String(64), nullable=True),
        sa.Column("broker_order_id", sa.String(128), nullable=True),
        sa.Column("decision", sa.JSON(), nullable=False),
        sa.Column("signal_date", sa.Date(), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("limit_price", sa.Numeric(24, 6), nullable=False),
        sa.Column("stop_price", sa.Float(), nullable=True),
        sa.Column("fee_rate", sa.Float(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("filled_quantity", sa.Integer(), nullable=False),
        sa.Column("gross", sa.Numeric(24, 6), nullable=False),
        sa.Column("fees", sa.Numeric(24, 6), nullable=False),
        sa.Column("tax", sa.Numeric(24, 6), nullable=False),
        sa.UniqueConstraint('intent_id'))
    op.create_index("ix_fujimoto_order_account_key", "fujimoto_order", ['account_key'])
    op.create_index("ix_fujimoto_order_cycle_id", "fujimoto_order", ['cycle_id'])
    op.create_table("fujimoto_fill",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("fujimoto_order.id"), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("resulting_state", sa.JSON(), nullable=False),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint('order_id', 'fingerprint', name="uq_fujimoto_fill"))
    op.create_index("ix_fujimoto_fill_order_id", "fujimoto_fill", ['order_id'])


def downgrade():
    """Remove only the additive Fujimoto objects on an explicitly chosen database."""
    op.drop_table("fujimoto_fill")
    op.drop_table("fujimoto_order")
    op.drop_table("fujimoto_cycle")
    op.drop_table("fujimoto_evidence")
    op.drop_table("fujimoto_config")
