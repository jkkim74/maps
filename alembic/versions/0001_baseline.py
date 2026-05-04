"""baseline: 전체 테이블 초기 생성

Revision ID: 0001_baseline
Revises:
Create Date: 2026-05-02

설계서 v2.6.2 §16 + v2.6.3 §10 의 12개 테이블 전체.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. security_metadata
    op.create_table(
        "security_metadata",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("ticker", sa.String(16), nullable=False, unique=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("market", sa.String(16), nullable=False),
        sa.Column("security_type", sa.String(16), nullable=False),
        sa.Column("listing_date", sa.Date, nullable=True),
        sa.Column("delisting_date", sa.Date, nullable=True),
        sa.Column("has_adjusted_price", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_security_metadata_ticker", "security_metadata", ["ticker"])

    # 2. universe_quality_log
    op.create_table(
        "universe_quality_log",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("ref_date", sa.Date, nullable=False),
        sa.Column("mode", sa.String(16), nullable=False, server_default="backtest"),
        sa.Column("total_candidates", sa.Integer, nullable=False),
        sa.Column("kept_count", sa.Integer, nullable=False),
        sa.Column("excluded_count", sa.Integer, nullable=False),
        sa.Column("rejection_ratio", sa.Float, nullable=False),
        sa.Column("alert_sent", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_universe_quality_log_ref_date", "universe_quality_log", ["ref_date"])

    # 3. collection_log
    op.create_table(
        "collection_log",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("ref_date", sa.Date, nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("items", sa.Integer, nullable=True),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_collection_log_ref_date", "collection_log", ["ref_date"])

    # 4. parameter_plateau_results
    op.create_table(
        "parameter_plateau_results",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("strategy_id", sa.String(64), nullable=False),
        sa.Column("run_date", sa.Date, nullable=False),
        sa.Column("total_combinations", sa.Integer, nullable=False),
        sa.Column("positive_combinations", sa.Integer, nullable=False),
        sa.Column("positive_ratio", sa.Float, nullable=False),
        sa.Column("grade", sa.String(4), nullable=False),
        sa.Column("best_params_json", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_parameter_plateau_results_strategy_id", "parameter_plateau_results", ["strategy_id"])

    # 5. walk_forward_results
    op.create_table(
        "walk_forward_results",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("strategy_id", sa.String(64), nullable=False),
        sa.Column("run_date", sa.Date, nullable=False),
        sa.Column("n_folds", sa.Integer, nullable=False),
        sa.Column("sharpe_mean", sa.Float, nullable=False),
        sa.Column("sharpe_std", sa.Float, nullable=False),
        sa.Column("negative_folds", sa.Integer, nullable=False),
        sa.Column("mean_g2p", sa.Float, nullable=False),
        sa.Column("passed", sa.Boolean, nullable=False),
        sa.Column("fail_reasons_json", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_walk_forward_results_strategy_id", "walk_forward_results", ["strategy_id"])

    # 6. monte_carlo_sequence_results
    op.create_table(
        "monte_carlo_sequence_results",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("strategy_id", sa.String(64), nullable=False),
        sa.Column("strategy_group", sa.String(32), nullable=False),
        sa.Column("run_date", sa.Date, nullable=False),
        sa.Column("n_simulations", sa.Integer, nullable=False),
        sa.Column("mdd_p95", sa.Float, nullable=False),
        sa.Column("mdd_limit", sa.Float, nullable=False),
        sa.Column("mc_within_limit", sa.Boolean, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_monte_carlo_sequence_results_strategy_id", "monte_carlo_sequence_results", ["strategy_id"])

    # 7. promotion_history
    op.create_table(
        "promotion_history",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("strategy_id", sa.String(64), nullable=False),
        sa.Column("from_stage", sa.String(32), nullable=False),
        sa.Column("to_stage", sa.String(32), nullable=False),
        sa.Column("tradeability_score", sa.Float, nullable=False),
        sa.Column("passed", sa.Boolean, nullable=False),
        sa.Column("fail_reasons_json", sa.Text, nullable=True),
        sa.Column("evaluated_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_promotion_history_strategy_id", "promotion_history", ["strategy_id"])

    # 8. tradeability_weight_log
    op.create_table(
        "tradeability_weight_log",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("strategy_id", sa.String(64), nullable=False),
        sa.Column("preset_name", sa.String(32), nullable=False),
        sa.Column("weights_json", sa.Text, nullable=False),
        sa.Column("score_before", sa.Float, nullable=True),
        sa.Column("score_after", sa.Float, nullable=False),
        sa.Column("changed_by", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_tradeability_weight_log_strategy_id", "tradeability_weight_log", ["strategy_id"])

    # 9. order_log
    op.create_table(
        "order_log",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("order_id", sa.String(64), nullable=False, unique=True),
        sa.Column("strategy_id", sa.String(64), nullable=True),
        sa.Column("ticker", sa.String(16), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("qty", sa.Integer, nullable=False),
        sa.Column("order_price", sa.Float, nullable=True),
        sa.Column("fill_price", sa.Float, nullable=True),
        sa.Column("fill_qty", sa.Integer, nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("broker", sa.String(16), nullable=True),
        sa.Column("mode", sa.String(16), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_order_log_order_id", "order_log", ["order_id"])
    op.create_index("ix_order_log_strategy_id", "order_log", ["strategy_id"])

    # 10. kill_switch_log
    op.create_table(
        "kill_switch_log",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("strategy_id", sa.String(64), nullable=True),
        sa.Column("event_type", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(64), nullable=False),
        sa.Column("value", sa.Text, nullable=True),
        sa.Column("new_entry_blocked", sa.Boolean, nullable=False, server_default="1"),
        sa.Column("approved_by", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_kill_switch_log_strategy_id", "kill_switch_log", ["strategy_id"])

    # 11. strategy_param_log
    op.create_table(
        "strategy_param_log",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("strategy_id", sa.String(64), nullable=False),
        sa.Column("params_json", sa.Text, nullable=False),
        sa.Column("effective_at", sa.Date, nullable=False),
        sa.Column("reason", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_strategy_param_log_strategy_id", "strategy_param_log", ["strategy_id"])

    # 12. cost_model_assumptions
    op.create_table(
        "cost_model_assumptions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("commission_rate", sa.Float, nullable=False),
        sa.Column("slippage_rate", sa.Float, nullable=False),
        sa.Column("tax_rate", sa.Float, nullable=False),
        sa.Column("effective_at", sa.Date, nullable=False),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("cost_model_assumptions")
    op.drop_table("strategy_param_log")
    op.drop_table("kill_switch_log")
    op.drop_table("order_log")
    op.drop_table("tradeability_weight_log")
    op.drop_table("promotion_history")
    op.drop_table("monte_carlo_sequence_results")
    op.drop_table("walk_forward_results")
    op.drop_table("parameter_plateau_results")
    op.drop_table("collection_log")
    op.drop_table("universe_quality_log")
    op.drop_table("security_metadata")
