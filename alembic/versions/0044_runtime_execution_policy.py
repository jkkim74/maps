"""Durable runtime execution policy (no permission backfill)."""
from alembic import op
import sqlalchemy as sa

revision = "0044_runtime_execution_policy"
down_revision = "0043_us_paper_trading"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create an empty table; existing environment defaults remain authoritative."""
    op.create_table("execution_policy",
        sa.Column("account_key", sa.String(64), primary_key=True),
        sa.Column("environment", sa.String(16), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("scope", sa.String(24), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.String(128), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False))


def downgrade() -> None:
    """Never discard a saved permission on rollback."""
    if op.get_bind().execute(sa.text("SELECT count(*) FROM execution_policy")).scalar():
        raise RuntimeError("Preserve populated execution_policy; disable orders before code rollback")
    op.drop_table("execution_policy")
