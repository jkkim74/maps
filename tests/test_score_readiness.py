from __future__ import annotations

import datetime as dt

from maps.common.models import CandidateSnapshot, MarketRegimeLog
from maps.market.regime import MarketRegimeCompositeScorer, MarketRegimeInput
from maps.ops.score_readiness import candidate_score_ready, market_score_ready
from maps.promotion.gate import PromotionGate, PromotionStage
from maps.strategy.base import StrategyType
from maps.strategy.scoring import StrategyAwareScoreCalculator, StrategyScoreInput


def test_partial_candidate_score_is_explicit_and_not_order_ready() -> None:
    result = StrategyAwareScoreCalculator().calculate(
        StrategyScoreInput(
            strategy_type=StrategyType.PULLBACK,
            liquidity_score=80,
            trend_strength=75,
            valuation_margin_score=70,
        )
    )
    assert result.final_score == 70
    assert result.coverage_ratio == 0.15
    assert result.score_status == "partial"
    assert result.score_ready is False
    assert set(result.missing_components or []) == {
        "support_quality", "volume_cooling", "trend_preservation", "supply_demand_score",
    }


def test_complete_candidate_score_is_order_ready() -> None:
    result = StrategyAwareScoreCalculator().calculate(
        StrategyScoreInput(
            strategy_type=StrategyType.PULLBACK,
            liquidity_score=80,
            trend_strength=75,
            valuation_margin_score=70,
            extra_scores={
                "support_quality": 80,
                "volume_cooling": 60,
                "trend_preservation": 75,
                "supply_demand_score": 65,
            },
        )
    )
    assert result.coverage_ratio == 1.0
    assert result.score_status == "complete"
    assert result.score_ready is True
    assert result.missing_components == []


def test_market_score_distinguishes_missing_from_measured_neutral() -> None:
    scorer = MarketRegimeCompositeScorer()
    partial = scorer.score(MarketRegimeInput(
        legacy_regime="mixed", vol_regime="normal", weekly_trend="pass",
        price_trend_score=41.15, volatility_score=50, foreign_fx_score=50,
    ))
    complete = scorer.score(MarketRegimeInput(
        legacy_regime="mixed", vol_regime="normal", weekly_trend="pass",
        price_trend_score=41.15, volatility_score=50, foreign_fx_score=50,
        measured_liquidity_score=50, measured_psychology_score=50,
    ))
    assert partial.score_ready is False
    assert partial.liquidity_score is None
    assert partial.psychology_score is None
    assert partial.coverage_ratio == 0.65
    assert complete.score_ready is True
    assert complete.coverage_ratio == 1.0


def test_candidate_gate_requires_exact_complete_market_and_candidate(db) -> None:
    ref_date = dt.date(2026, 8, 11)
    candidate = CandidateSnapshot(
        ref_date=ref_date, strategy_id="pullback_v3", ticker="005930",
        name="Samsung Electronics", market="KOSPI", factor_score=80,
        trend_strength=75, final_score=78, score_coverage_ratio=1.0,
        score_status="complete", score_ready=True, market_score_ready=True,
    )
    db.add(candidate)
    db.commit()
    assert market_score_ready(db, ref_date) == (False, "market_score_missing")
    assert candidate_score_ready(db, candidate) == (False, "market_score_missing")

    db.add(MarketRegimeLog(
        ref_date=ref_date, raw_regime="mixed", applied_regime="mixed",
        score_coverage_ratio=1.0, score_status="complete", score_ready=True,
    ))
    db.commit()
    assert candidate_score_ready(db, candidate) == (True, None)


def test_promotion_rejects_incomplete_measured_scores(db) -> None:
    decision = PromotionGate(db).evaluate(
        "pullback_v3",
        {
            "robustness": 1.0, "risk": 1.0, "recovery": 1.0, "return": 1.0,
            "market_data_ready": False, "candidate_data_ready": False,
        },
        PromotionStage.RESEARCH,
    )
    assert decision.passed is False
    assert any("100%" in reason for reason in decision.reasons)
