"""Durable execution intents, account observations and coherent validation evidence."""
from alembic import op
import sqlalchemy as sa

revision = "0038_execution_safety"
down_revision = "0037_close_report_evidence"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("order_log", sa.Column("intent_id", sa.String(length=36), nullable=True, primary_key=False))
    op.add_column("order_log", sa.Column("account_key", sa.String(length=64), nullable=True, primary_key=False))
    op.add_column("order_log", sa.Column("environment", sa.String(length=16), nullable=True, primary_key=False))
    op.add_column("order_log", sa.Column("code_hash", sa.String(length=64), nullable=True, primary_key=False))
    op.add_column("order_log", sa.Column("params_hash", sa.String(length=64), nullable=True, primary_key=False))
    op.create_index("ix_order_log_account_key", "order_log", ['account_key'])
    op.create_index("ix_order_log_intent_id", "order_log", ['intent_id'])
    op.add_column("kill_switch_log", sa.Column("account_key", sa.String(length=64), nullable=True, primary_key=False))
    op.add_column("kill_switch_log", sa.Column("scope", sa.String(length=16), nullable=True, primary_key=False))
    op.create_index("ix_kill_switch_log_account_key", "kill_switch_log", ['account_key'])
    op.add_column("analysis_pick", sa.Column("execution_version", sa.Integer(), nullable=False, primary_key=False, server_default="0"))
    op.add_column("parameter_plateau_results", sa.Column("validation_run_id", sa.String(length=36), nullable=True, primary_key=False))
    op.create_index("ix_parameter_plateau_results_validation_run_id", "parameter_plateau_results", ['validation_run_id'])
    op.add_column("walk_forward_results", sa.Column("validation_run_id", sa.String(length=36), nullable=True, primary_key=False))
    op.create_index("ix_walk_forward_results_validation_run_id", "walk_forward_results", ['validation_run_id'])
    op.add_column("monte_carlo_sequence_results", sa.Column("validation_run_id", sa.String(length=36), nullable=True, primary_key=False))
    op.create_index("ix_monte_carlo_sequence_results_validation_run_id", "monte_carlo_sequence_results", ['validation_run_id'])
    op.add_column("promotion_history", sa.Column("validation_run_id", sa.String(length=36), nullable=True, primary_key=False))
    op.create_index("ix_promotion_history_validation_run_id", "promotion_history", ['validation_run_id'])
    op.create_table("order_intent",
        sa.Column("id", sa.String(length=36), nullable=False, primary_key=True),
        sa.Column("account_key", sa.String(length=64), nullable=False, primary_key=False),
        sa.Column("environment", sa.String(length=16), nullable=False, primary_key=False),
        sa.Column("event_key", sa.String(length=128), nullable=False, primary_key=False),
        sa.Column("strategy_id", sa.String(length=64), nullable=False, primary_key=False),
        sa.Column("ticker", sa.String(length=16), nullable=False, primary_key=False),
        sa.Column("side", sa.String(length=8), nullable=False, primary_key=False),
        sa.Column("status", sa.String(length=24), nullable=False, primary_key=False),
        sa.Column("request", sa.JSON(), nullable=False, primary_key=False),
        sa.Column("broker_order_id", sa.String(length=64), nullable=True, primary_key=False),
        sa.Column("quantity", sa.Integer(), nullable=False, primary_key=False),
        sa.Column("filled_quantity", sa.Integer(), nullable=False, primary_key=False),
        sa.Column("reserved_amount", sa.Numeric(precision=24, scale=4), nullable=False, primary_key=False),
        sa.Column("reserved_quantity", sa.Integer(), nullable=False, primary_key=False),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, primary_key=False),
        sa.Column("validation_run_id", sa.String(length=36), nullable=True, primary_key=False),
        sa.Column("version", sa.Integer(), nullable=False, primary_key=False),
        sa.Column("valid_until", sa.DateTime(), nullable=False, primary_key=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, primary_key=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False, primary_key=False),
        sa.UniqueConstraint('account_key', 'event_key', name="uq_order_intent_event"))
    op.create_index("ix_order_intent_account_key", "order_intent", ['account_key'])
    op.create_index("ix_order_intent_broker_order_id", "order_intent", ['broker_order_id'])
    op.create_index("ix_order_intent_status", "order_intent", ['status'])
    op.create_index("ix_order_intent_ticker", "order_intent", ['ticker'])
    op.create_table("execution_account_state",
        sa.Column("account_key", sa.String(length=64), nullable=False, primary_key=True),
        sa.Column("environment", sa.String(length=16), nullable=False, primary_key=False),
        sa.Column("status", sa.String(length=16), nullable=False, primary_key=False),
        sa.Column("block_reasons", sa.JSON(), nullable=False, primary_key=False),
        sa.Column("checked_at", sa.DateTime(), nullable=True, primary_key=False),
        sa.Column("last_complete_at", sa.DateTime(), nullable=True, primary_key=False),
        sa.Column("ref_date", sa.Date(), nullable=True, primary_key=False),
        sa.Column("value_index", sa.Numeric(precision=24, scale=10), nullable=True, primary_key=False),
        sa.Column("high_water", sa.Numeric(precision=24, scale=10), nullable=True, primary_key=False),
        sa.Column("day_open_index", sa.Numeric(precision=24, scale=10), nullable=True, primary_key=False),
        sa.Column("daily_return", sa.Numeric(precision=24, scale=10), nullable=True, primary_key=False),
        sa.Column("drawdown", sa.Numeric(precision=24, scale=10), nullable=True, primary_key=False),
        sa.Column("killed", sa.Boolean(), nullable=False, primary_key=False),
        sa.Column("version", sa.Integer(), nullable=False, primary_key=False))
    op.create_table("account_observation",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("account_key", sa.String(length=64), nullable=False, primary_key=False),
        sa.Column("observed_at", sa.DateTime(), nullable=False, primary_key=False),
        sa.Column("ref_date", sa.Date(), nullable=False, primary_key=False),
        sa.Column("nav", sa.Numeric(precision=24, scale=4), nullable=False, primary_key=False),
        sa.Column("cash", sa.Numeric(precision=24, scale=4), nullable=False, primary_key=False),
        sa.Column("evidence", sa.JSON(), nullable=False, primary_key=False),
        sa.Column("complete", sa.Boolean(), nullable=False, primary_key=False))
    op.create_index("ix_account_observation_account_key", "account_observation", ['account_key'])
    op.create_index("ix_account_observation_observed_at", "account_observation", ['observed_at'])
    op.create_table("account_adjustment",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("account_key", sa.String(length=64), nullable=False, primary_key=False),
        sa.Column("observation_id", sa.Integer(), nullable=False, primary_key=False),
        sa.Column("status", sa.String(length=16), nullable=False, primary_key=False),
        sa.Column("kind", sa.String(length=32), nullable=True, primary_key=False),
        sa.Column("amount", sa.Numeric(precision=24, scale=4), nullable=False, primary_key=False),
        sa.Column("evidence", sa.JSON(), nullable=False, primary_key=False),
        sa.Column("resolution", sa.JSON(), nullable=True, primary_key=False),
        sa.Column("version", sa.Integer(), nullable=False, primary_key=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, primary_key=False),
        sa.UniqueConstraint('observation_id'))
    op.create_index("ix_account_adjustment_account_key", "account_adjustment", ['account_key'])
    op.create_table("validation_run",
        sa.Column("id", sa.String(length=36), nullable=False, primary_key=True),
        sa.Column("strategy_id", sa.String(length=64), nullable=False, primary_key=False),
        sa.Column("run_date", sa.Date(), nullable=False, primary_key=False),
        sa.Column("status", sa.String(length=16), nullable=False, primary_key=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False, primary_key=False),
        sa.Column("params_hash", sa.String(length=64), nullable=False, primary_key=False),
        sa.Column("data_hash", sa.String(length=64), nullable=False, primary_key=False),
        sa.Column("input_snapshot", sa.LargeBinary(), nullable=False, primary_key=False),
        sa.Column("manifest", sa.JSON(), nullable=False, primary_key=False),
        sa.Column("metrics", sa.JSON(), nullable=True, primary_key=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, primary_key=False))
    op.create_index("ix_validation_run_strategy_id", "validation_run", ['strategy_id'])
    op.create_table("execution_safety_event",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("account_key", sa.String(length=64), nullable=False, primary_key=False),
        sa.Column("reason_code", sa.String(length=64), nullable=False, primary_key=False),
        sa.Column("details", sa.JSON(), nullable=False, primary_key=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, primary_key=False),
        sa.Column("delivered_at", sa.DateTime(), nullable=True, primary_key=False),
        sa.Column("acknowledged_at", sa.DateTime(), nullable=True, primary_key=False),
        sa.Column("attempts", sa.Integer(), nullable=False, primary_key=False),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=True, primary_key=False))
    op.create_index("ix_execution_safety_event_account_key", "execution_safety_event", ['account_key'])


def downgrade():
    op.drop_table("execution_safety_event")
    op.drop_table("validation_run")
    op.drop_table("account_adjustment")
    op.drop_table("account_observation")
    op.drop_table("execution_account_state")
    op.drop_table("order_intent")
    op.drop_index("ix_promotion_history_validation_run_id", table_name="promotion_history")
    op.drop_column("promotion_history", "validation_run_id")
    op.drop_index("ix_monte_carlo_sequence_results_validation_run_id", table_name="monte_carlo_sequence_results")
    op.drop_column("monte_carlo_sequence_results", "validation_run_id")
    op.drop_index("ix_walk_forward_results_validation_run_id", table_name="walk_forward_results")
    op.drop_column("walk_forward_results", "validation_run_id")
    op.drop_index("ix_parameter_plateau_results_validation_run_id", table_name="parameter_plateau_results")
    op.drop_column("parameter_plateau_results", "validation_run_id")
    op.drop_column("analysis_pick", "execution_version")
    op.drop_index("ix_kill_switch_log_account_key", table_name="kill_switch_log")
    op.drop_column("kill_switch_log", "scope")
    op.drop_column("kill_switch_log", "account_key")
    op.drop_index("ix_order_log_account_key", table_name="order_log")
    op.drop_index("ix_order_log_intent_id", table_name="order_log")
    op.drop_column("order_log", "params_hash")
    op.drop_column("order_log", "code_hash")
    op.drop_column("order_log", "environment")
    op.drop_column("order_log", "account_key")
    op.drop_column("order_log", "intent_id")
