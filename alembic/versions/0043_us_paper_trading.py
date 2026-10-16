"""Dedicated Decimal USD paper ledger; no domestic or external holding adoption."""
from alembic import op
import sqlalchemy as sa

revision = '0043_us_paper_trading'
down_revision = '0042_paper_cost_settlement'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create isolated USD ledger and independent durable mock truth."""
    op.create_table('us_account_state',
        sa.Column('account_key', sa.String(length=64), nullable=False, primary_key=True),
        sa.Column('entry_stopped', sa.Boolean(), nullable=False),
        sa.Column('last_sync_at', sa.DateTime(), nullable=True),
        sa.Column('blocked_reason', sa.String(length=256), nullable=True),
        sa.Column('session_date', sa.String(length=10), nullable=True),
        sa.Column('session_equity', sa.Numeric(precision=24, scale=6), nullable=True),
        sa.Column('observed_cash', sa.Numeric(precision=24, scale=6), nullable=True),
        sa.Column('observed_positions', sa.JSON(), nullable=False),
        sa.Column('observed_flow', sa.Numeric(precision=24, scale=6), nullable=False),
        sa.Column('observed_fills', sa.JSON(), nullable=False),
        sa.Column('loss_stopped', sa.Boolean(), nullable=False),
        sa.Column('snapshot', sa.JSON(), nullable=False),
    )
    op.create_table('us_order_ledger',
        sa.Column('id', sa.String(length=64), nullable=False, primary_key=True),
        sa.Column('account_key', sa.String(length=64), nullable=False),
        sa.Column('request_id', sa.String(length=128), nullable=False),
        sa.Column('fingerprint', sa.String(length=64), nullable=False),
        sa.Column('broker_order_id', sa.String(length=128), nullable=True),
        sa.Column('instance_id', sa.String(length=128), nullable=False),
        sa.Column('source', sa.String(length=32), nullable=False),
        sa.Column('strategy_id', sa.String(length=128), nullable=False),
        sa.Column('evidence', sa.JSON(), nullable=False),
        sa.Column('ticker', sa.String(length=16), nullable=False),
        sa.Column('exchange', sa.String(length=16), nullable=False),
        sa.Column('side', sa.String(length=8), nullable=False),
        sa.Column('quantity', sa.Integer(), nullable=False),
        sa.Column('limit_price', sa.Numeric(precision=24, scale=6), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('filled_quantity', sa.Integer(), nullable=False),
        sa.Column('gross', sa.Numeric(precision=24, scale=6), nullable=False),
        sa.Column('commission', sa.Numeric(precision=24, scale=6), nullable=True),
        sa.Column('tax', sa.Numeric(precision=24, scale=6), nullable=True),
        sa.Column('costs_confirmed', sa.Boolean(), nullable=False),
        sa.Column('reserved_usd', sa.Numeric(precision=24, scale=6), nullable=False),
        sa.Column('reserved_quantity', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('account_key', 'request_id', name='uq_us_order_request'),
    )
    op.create_index('ix_us_order_ledger_account_key', 'us_order_ledger', ['account_key'])
    op.create_table('us_owned_position',
        sa.Column('id', sa.Integer(), nullable=False, primary_key=True),
        sa.Column('account_key', sa.String(length=64), nullable=False),
        sa.Column('instance_id', sa.String(length=128), nullable=False),
        sa.Column('ticker', sa.String(length=16), nullable=False),
        sa.Column('exchange', sa.String(length=16), nullable=False),
        sa.Column('quantity', sa.Integer(), nullable=False),
        sa.Column('cost_basis', sa.Numeric(precision=24, scale=6), nullable=False),
        sa.UniqueConstraint('account_key', 'instance_id', 'ticker', 'exchange', name='uq_us_owned_position'),
    )
    op.create_index('ix_us_owned_position_account_key', 'us_owned_position', ['account_key'])
    op.create_table('us_execution_event',
        sa.Column('id', sa.Integer(), nullable=False, primary_key=True),
        sa.Column('account_key', sa.String(length=64), nullable=False),
        sa.Column('order_id', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('event_type', sa.String(length=64), nullable=False),
        sa.Column('actor', sa.String(length=128), nullable=False),
        sa.Column('message', sa.Text(), nullable=False),
    )
    op.create_index('ix_us_execution_event_account_key', 'us_execution_event', ['account_key'])
    op.create_table('us_mock_truth',
        sa.Column('account_key', sa.String(length=64), nullable=False, primary_key=True),
        sa.Column('cash', sa.Numeric(precision=24, scale=6), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
    )


def downgrade() -> None:
    """Only empty ledgers may be dropped; unresolved trading records are evidence."""
    bind = op.get_bind()
    if bind.execute(sa.text('SELECT COUNT(*) FROM us_account_state')).scalar():
        raise RuntimeError('Refusing to drop a nonempty US trading ledger')
    if bind.execute(sa.text('SELECT COUNT(*) FROM us_order_ledger')).scalar():
        raise RuntimeError('Refusing to drop a nonempty US trading ledger')
    if bind.execute(sa.text('SELECT COUNT(*) FROM us_owned_position')).scalar():
        raise RuntimeError('Refusing to drop a nonempty US trading ledger')
    if bind.execute(sa.text('SELECT COUNT(*) FROM us_execution_event')).scalar():
        raise RuntimeError('Refusing to drop a nonempty US trading ledger')
    if bind.execute(sa.text('SELECT COUNT(*) FROM us_mock_truth')).scalar():
        raise RuntimeError('Refusing to drop a nonempty US trading ledger')
    op.drop_table('us_mock_truth')
    op.drop_table('us_execution_event')
    op.drop_table('us_owned_position')
    op.drop_table('us_order_ledger')
    op.drop_table('us_account_state')
