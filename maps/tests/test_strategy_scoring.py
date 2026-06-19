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
    assert strong.component_scores["new_high_score"] == 95.0


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


def test_missing_strategy_components_use_neutral_scores():
    result = StrategyAwareScoreCalculator().calculate(
        StrategyScoreInput(
            strategy_type=StrategyType.PULLBACK,
            liquidity_score=50.0,
            trend_strength=50.0,
        )
    )

    assert result.final_score == 60.0
    assert result.component_scores["valuation_margin_score"] == 50.0
    assert result.score_type == "STRATEGY_AWARE"
