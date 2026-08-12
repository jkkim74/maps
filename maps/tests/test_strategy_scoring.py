from maps.strategy.base import StrategyType
from maps.strategy.scoring import (
    LegacyFinalScoreCalculator,
    StrategyAwareScoreCalculator,
    StrategyScoreInput,
)


def test_legacy_scoring_keeps_original_formula_without_ai():
    result = LegacyFinalScoreCalculator().calculate(
        factor_score=80.0,
        trend_strength=50.0,
        strategy_type=StrategyType.PULLBACK,
    )

    assert result.score_type == "LEGACY"
    assert result.final_score == 68.0
    assert result.component_scores["liquidity_score"] == 80.0
    assert result.component_scores["trend_strength"] == 50.0


def test_breakout_prefers_high_liquidity_and_trend():
    calc = StrategyAwareScoreCalculator()

    strong = calc.calculate(
        StrategyScoreInput(
            strategy_type=StrategyType.BREAKOUT,
            liquidity_score=95.0,
            trend_strength=90.0,
            ts_bucket="S5",
            extra_scores={"institutional_foreign_flow": 80.0},
        )
    )
    weak = calc.calculate(
        StrategyScoreInput(
            strategy_type=StrategyType.BREAKOUT,
            liquidity_score=30.0,
            trend_strength=35.0,
            ts_bucket="S2",
            extra_scores={"institutional_foreign_flow": 50.0},
        )
    )

    assert strong.final_score > weak.final_score
    assert "new_high_score" not in strong.component_scores
    assert "new_high_score" in strong.missing_components
    assert strong.score_ready is False


def test_contrarian_quality_prefers_valuation_and_earnings_revision():
    calc = StrategyAwareScoreCalculator()

    cheap_improving = calc.calculate(
        StrategyScoreInput(
            strategy_type=StrategyType.CONTRARIAN_QUALITY,
            liquidity_score=40.0,
            trend_strength=30.0,
            valuation_margin_score=85.0,
            extra_scores={
                "earnings_revision_score": 80.0,
                "accumulation_flow_score": 70.0,
                "technical_bottom_score": 75.0,
            },
        )
    )
    expensive_weak = calc.calculate(
        StrategyScoreInput(
            strategy_type=StrategyType.CONTRARIAN_QUALITY,
            liquidity_score=80.0,
            trend_strength=85.0,
            valuation_margin_score=35.0,
            extra_scores={
                "earnings_revision_score": 30.0,
                "accumulation_flow_score": 40.0,
                "technical_bottom_score": 20.0,
            },
        )
    )

    assert cheap_improving.final_score > expensive_weak.final_score
    assert expensive_weak.excluded_reason == "valuation_margin_below_contrarian_threshold"
    assert expensive_weak.final_score <= 39.0


def test_legacy_reason_contains_per_ticker_measurements():
    """근거 문자열은 공식 설명이 아니라 종목별 실측치여야 한다 — 매매일지의 재료다."""
    result = LegacyFinalScoreCalculator().calculate(
        factor_score=100.0,
        trend_strength=29.93,
        strategy_type=StrategyType.MOMENTUM,
        ts_bucket="S2",
    )

    assert "100.0" in result.reason
    assert "29.9" in result.reason
    assert "(S2)" in result.reason
    assert f"{result.final_score:.2f}" in result.reason


def test_strategy_aware_reason_lists_component_values_and_missing_inputs():
    result = StrategyAwareScoreCalculator().calculate(
        StrategyScoreInput(
            strategy_type=StrategyType.PULLBACK,
            liquidity_score=70.0,
            trend_strength=60.0,
            valuation_margin_score=55.0,
        )
    )

    assert "valuation_margin_score=55.0" in result.reason
    assert "partial coverage=0.15" in result.reason
    assert "missing=" in result.reason
    assert "supply_demand_score" in result.reason


def test_missing_strategy_components_are_unavailable_not_neutral():
    result = StrategyAwareScoreCalculator().calculate(
        StrategyScoreInput(
            strategy_type=StrategyType.PULLBACK,
            liquidity_score=50.0,
            trend_strength=50.0,
        )
    )

    assert result.final_score == 50.0
    assert result.component_scores == {}
    assert result.score_type == "STRATEGY_AWARE"
    assert result.score_status == "unavailable"
    assert result.score_ready is False
