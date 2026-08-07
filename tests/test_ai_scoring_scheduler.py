"""Scheduler integration tests for the one-pass Phase 2 scoring service."""

from __future__ import annotations

import datetime as dt
from unittest.mock import Mock

import numpy as np
import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from maps.ai.scoring_service import AIScoringRunSummary, AIStockScoringService
from maps.ai.technical_scorer import AITechnicalScorer
from maps.common.db import Base
from maps.common.models import CandidateSnapshot
from maps.common.settings import MapsSettings
from maps.data.security_repo import Security
from maps.ops.scheduler import OperationalPipeline, TickerContext


def _frame(ref_date: dt.date) -> pd.DataFrame:
    """Return deterministic OHLCV history for scheduler snapshot tests."""
    rng = np.random.default_rng(11)
    close = 30_000 + np.cumsum(rng.normal(20, 200, 80))
    return pd.DataFrame(
        {
            "open": close * 0.99,
            "high": close * 1.01,
            "low": close * 0.98,
            "close": close,
            "volume": rng.integers(100_000, 800_000, 80),
        },
        index=pd.bdate_range(end=ref_date, periods=80),
    )


def test_save_candidate_snapshot_never_calls_bedrock(monkeypatch, db) -> None:
    """Per-strategy persistence remains rule-only even in rerank mode."""
    ref_date = dt.date(2026, 8, 7)
    stock = Security(
        ticker="005930",
        name="삼성전자",
        market="KOSPI",
        security_type="STOCK",
        turnover_cache={ref_date: 10_000_000_000.0},
    )
    frame = _frame(ref_date)
    contexts = {
        stock.ticker: TickerContext(
            frame=frame,
            trend_strength=72.5,
            ts_bucket="S4",
            close=float(frame["close"].iloc[-1]),
            atr14=500.0,
        )
    }
    monkeypatch.setattr(
        AITechnicalScorer,
        "score",
        Mock(side_effect=AssertionError("Bedrock must run after all strategies")),
    )
    pipeline = OperationalPipeline(
        settings=MapsSettings(maps_ai_scoring_mode="rerank")
    )

    pipeline._save_candidate_snapshot(
        db, ref_date, "pullback_v3", [stock], contexts=contexts
    )
    row = db.query(CandidateSnapshot).one()

    assert row.rule_score == row.recommendation_score == row.final_score
    assert row.score_source == "RULE"
    assert row.ai_technical_score is None


def test_generate_candidates_applies_one_global_ai_pass(monkeypatch) -> None:
    """Candidate generation invokes the global service exactly once per job."""
    apply = Mock(
        return_value=AIScoringRunSummary(
            targets=2,
            calls=2,
            cache_hits=0,
            successes=2,
            failures=0,
            skipped_limit=0,
            input_tokens=800,
            output_tokens=160,
        )
    )
    monkeypatch.setattr(AIStockScoringService, "apply", apply)
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    try:
        pipeline = OperationalPipeline(
            settings=MapsSettings(
                maps_broker_mode="mock",
                maps_data_provider="mock",
                maps_live_trading_enabled=False,
                maps_market_regime_override="strong",
                maps_ai_scoring_mode="rerank",
            ),
            session_factory=factory,
        )
        pipeline.collect_data()

        run = pipeline.generate_candidates()

        assert apply.call_count == 1
        assert run.details["ai_calls"] == 2
        assert run.details["ai_input_tokens"] == 800
        assert run.details["ai_output_tokens"] == 160
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()
