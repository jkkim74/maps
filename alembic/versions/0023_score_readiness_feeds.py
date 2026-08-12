"""add score readiness and market feed snapshots

Revision ID: 0023_score_readiness_feeds
Revises: 0022_stock_analysis_history
Create Date: 2026-08-12
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0023_score_readiness_feeds"
down_revision: Union[str, None] = "0022_stock_analysis_history"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create feed audit tables and fail-closed score metadata."""
    op.create_table(
        "investor_flow_snapshot",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("ticker", sa.String(16), nullable=False),
        sa.Column("market", sa.String(16), nullable=False),
        sa.Column("foreign_net_value", sa.Float(), nullable=True),
        sa.Column("institutional_net_value", sa.Float(), nullable=True),
        sa.Column("individual_net_value", sa.Float(), nullable=True),
        sa.Column("source", sa.String(32), nullable=False, server_default="pykrx"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("date", "ticker", name="uq_investor_flow_date_ticker"),
    )
    op.create_index("ix_investor_flow_snapshot_date", "investor_flow_snapshot", ["date"])
    op.create_index("ix_investor_flow_snapshot_ticker", "investor_flow_snapshot", ["ticker"])
    op.create_table(
        "market_news_sentiment",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("ref_date", sa.Date(), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("label", sa.String(16), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("article_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("positive_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("neutral_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("negative_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(24), nullable=False, server_default="unavailable"),
        sa.Column("headlines", sa.JSON(), nullable=True),
        sa.Column("model_id", sa.String(128), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("ref_date", name="uq_market_news_sentiment_ref_date"),
    )
    op.create_index("ix_market_news_sentiment_ref_date", "market_news_sentiment", ["ref_date"])

    for name, type_, default in (
        ("component_sources", sa.JSON(), None),
        ("missing_components", sa.JSON(), None),
        ("score_coverage_ratio", sa.Float(), "0"),
        ("score_status", sa.String(16), "unavailable"),
        ("score_ready", sa.Boolean(), "false"),
        ("market_score_ready", sa.Boolean(), "false"),
    ):
        op.add_column(
            "candidate_snapshot",
            sa.Column(name, type_, nullable=default is None, server_default=default),
        )

    for name, type_, default in (
        ("final_market_score", sa.Float(), None),
        ("composite_regime", sa.String(16), None),
        ("policy_regime", sa.String(16), None),
        ("kospi_ts", sa.Float(), None),
        ("entry_limit_ratio", sa.Float(), None),
        ("market_mode", sa.String(32), None),
        ("score_reason", sa.String(1000), None),
        ("score_coverage_ratio", sa.Float(), "0"),
        ("score_status", sa.String(16), "unavailable"),
        ("score_ready", sa.Boolean(), "false"),
        ("factor_scores", sa.JSON(), None),
        ("factor_sources", sa.JSON(), None),
        ("measured_factors", sa.JSON(), None),
        ("missing_factors", sa.JSON(), None),
    ):
        op.add_column(
            "market_regime_log",
            sa.Column(name, type_, nullable=default is None, server_default=default),
        )


def downgrade() -> None:
    """Remove score readiness and feed snapshots."""
    for name in (
        "missing_factors", "measured_factors", "factor_sources", "factor_scores",
        "score_ready", "score_status", "score_coverage_ratio", "score_reason",
        "market_mode", "entry_limit_ratio", "kospi_ts", "policy_regime",
        "composite_regime", "final_market_score",
    ):
        op.drop_column("market_regime_log", name)
    for name in (
        "market_score_ready", "score_ready", "score_status", "score_coverage_ratio",
        "missing_components", "component_sources",
    ):
        op.drop_column("candidate_snapshot", name)
    op.drop_index("ix_market_news_sentiment_ref_date", table_name="market_news_sentiment")
    op.drop_table("market_news_sentiment")
    op.drop_index("ix_investor_flow_snapshot_ticker", table_name="investor_flow_snapshot")
    op.drop_index("ix_investor_flow_snapshot_date", table_name="investor_flow_snapshot")
    op.drop_table("investor_flow_snapshot")
