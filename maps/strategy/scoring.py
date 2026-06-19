"""Final score calculators for candidate ranking."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from maps.strategy.base import StrategyType


class ScoreType(str, Enum):
    LEGACY = "LEGACY"
    STRATEGY_AWARE = "STRATEGY_AWARE"


@dataclass(frozen=True)
class ScoreResult:
    final_score: float
    score_type: str
    strategy_type: str
    component_scores: dict[str, float]
    reason: str
    excluded_reason: str | None = None


@dataclass(frozen=True)
class StrategyScoreInput:
    strategy_type: StrategyType | str | None
    liquidity_score: float
    trend_strength: float
    ts_bucket: str | None = None
    valuation_margin_score: float | None = None
    ai_technical_score: float | None = None
    ai_weight: float = 0.0
    extra_scores: dict[str, Any] | None = None


def _normalize(value: float | int | None, default: float = 50.0) -> float:
    if value is None:
        return default
    try:
        return round(max(0.0, min(float(value), 100.0)), 2)
    except (TypeError, ValueError):
        return default


def _strategy_type_value(strategy_type: StrategyType | str | None) -> str:
    if isinstance(strategy_type, StrategyType):
        return strategy_type.value
    if isinstance(strategy_type, str) and strategy_type:
        return strategy_type
    return StrategyType.MOMENTUM.value


def _weighted_sum(components: dict[str, float], weights: dict[str, float]) -> float:
    return round(sum(_normalize(components.get(name)) * weight for name, weight in weights.items()), 2)


class LegacyFinalScoreCalculator:
    """Preserve the original MAPS ranking formula."""

    def calculate(
        self,
        *,
        factor_score: float,
        trend_strength: float,
        ai_technical_score: float | None = None,
        ai_weight: float = 0.0,
        strategy_type: StrategyType | str | None = None,
    ) -> ScoreResult:
        factor = _normalize(factor_score, default=0.0)
        trend = _normalize(trend_strength)
        base_score = 0.6 * factor + 0.4 * trend
        components = {
            "liquidity_score": factor,
            "trend_strength": trend,
        }
        reason = "legacy formula: liquidity 60%, trend_strength 40%"
        if ai_technical_score is not None:
            ai_score = _normalize(ai_technical_score)
            weight = max(0.0, min(float(ai_weight), 1.0))
            final_score = round((1.0 - weight) * base_score + weight * ai_score, 2)
            components["ai_technical_score"] = ai_score
            reason = (
                "legacy formula with AI overlay: "
                f"base {round(base_score, 2)} at {round((1.0 - weight) * 100, 1)}%, "
                f"AI at {round(weight * 100, 1)}%"
            )
        else:
            final_score = round(base_score, 2)

        return ScoreResult(
            final_score=final_score,
            score_type=ScoreType.LEGACY.value,
            strategy_type=_strategy_type_value(strategy_type),
            component_scores=components,
            reason=reason,
        )


class StrategyAwareScoreCalculator:
    """Apply strategy-specific score formulas with neutral placeholders."""

    _BREAKOUT_WEIGHTS = {
        "liquidity_score": 0.30,
        "trend_strength": 0.30,
        "new_high_score": 0.20,
        "institutional_foreign_flow": 0.20,
    }
    _PULLBACK_WEIGHTS = {
        "support_quality": 0.30,
        "volume_cooling": 0.20,
        "trend_preservation": 0.20,
        "supply_demand_score": 0.15,
        "valuation_margin_score": 0.15,
    }
    _CONTRARIAN_WEIGHTS = {
        "valuation_margin_score": 0.30,
        "earnings_revision_score": 0.25,
        "crowd_neglect_score": 0.20,
        "accumulation_flow_score": 0.15,
        "technical_bottom_score": 0.10,
    }
    _MULTI_ASSET_WEIGHTS = {
        "asset_trend_score": 0.40,
        "volatility_adjusted_momentum": 0.30,
        "macro_liquidity_score": 0.20,
        "risk_score": 0.10,
    }

    def calculate(self, score_input: StrategyScoreInput) -> ScoreResult:
        strategy_type = _strategy_type_value(score_input.strategy_type)
        extra = score_input.extra_scores or {}

        if strategy_type == StrategyType.BREAKOUT.value:
            components = self._breakout_components(score_input, extra)
            score = _weighted_sum(components, self._BREAKOUT_WEIGHTS)
            reason = "breakout formula: liquidity/trend/new-high/foreign-flow"
            excluded_reason = None
        elif strategy_type == StrategyType.PULLBACK.value:
            components = self._pullback_components(score_input, extra)
            score = _weighted_sum(components, self._PULLBACK_WEIGHTS)
            reason = "pullback formula: support/cooling/trend/supply-demand/valuation"
            excluded_reason = None
        elif strategy_type == StrategyType.CONTRARIAN_QUALITY.value:
            components = self._contrarian_components(score_input, extra)
            raw_score = _weighted_sum(components, self._CONTRARIAN_WEIGHTS)
            excluded_reason = None
            if components["valuation_margin_score"] < 60.0:
                excluded_reason = "valuation_margin_below_contrarian_threshold"
                score = min(raw_score, 39.0)
            else:
                score = raw_score
            reason = "contrarian_quality formula: valuation/earnings/neglect/accumulation/bottom"
        elif strategy_type == StrategyType.MULTI_ASSET_TREND.value:
            components = self._multi_asset_components(score_input, extra)
            score = _weighted_sum(components, self._MULTI_ASSET_WEIGHTS)
            reason = "multi_asset_trend formula: asset trend/vol-adjusted momentum/macro liquidity/risk"
            excluded_reason = None
        else:
            components = self._momentum_components(score_input, extra)
            score = _weighted_sum(components, {"liquidity_score": 0.50, "trend_strength": 0.50})
            reason = "default strategy-aware formula: liquidity 50%, trend_strength 50%"
            excluded_reason = None

        ai_score = _normalize(score_input.ai_technical_score) if score_input.ai_technical_score is not None else None
        if ai_score is not None and score_input.ai_weight > 0:
            weight = max(0.0, min(float(score_input.ai_weight), 1.0))
            score = round((1.0 - weight) * score + weight * ai_score, 2)
            components["ai_technical_score"] = ai_score
            reason = f"{reason}; AI technical overlay weight={round(weight, 2)}"

        return ScoreResult(
            final_score=round(score, 2),
            score_type=ScoreType.STRATEGY_AWARE.value,
            strategy_type=strategy_type,
            component_scores=components,
            reason=reason,
            excluded_reason=excluded_reason,
        )

    def _breakout_components(self, score_input: StrategyScoreInput, extra: dict[str, Any]) -> dict[str, float]:
        trend = _normalize(score_input.trend_strength)
        return {
            "liquidity_score": _normalize(score_input.liquidity_score, default=0.0),
            "trend_strength": trend,
            "new_high_score": _normalize(extra.get("new_high_score"), self._new_high_score(score_input.ts_bucket, trend)),
            "institutional_foreign_flow": _normalize(extra.get("institutional_foreign_flow")),
        }

    def _pullback_components(self, score_input: StrategyScoreInput, extra: dict[str, Any]) -> dict[str, float]:
        trend = _normalize(score_input.trend_strength)
        liquidity = _normalize(score_input.liquidity_score, default=0.0)
        return {
            "support_quality": _normalize(extra.get("support_quality"), trend),
            "volume_cooling": _normalize(extra.get("volume_cooling"), 100.0 - max(0.0, liquidity - 50.0)),
            "trend_preservation": _normalize(extra.get("trend_preservation"), trend),
            "supply_demand_score": _normalize(extra.get("supply_demand_score")),
            "valuation_margin_score": _normalize(score_input.valuation_margin_score),
        }

    def _contrarian_components(self, score_input: StrategyScoreInput, extra: dict[str, Any]) -> dict[str, float]:
        trend = _normalize(score_input.trend_strength)
        return {
            "valuation_margin_score": _normalize(score_input.valuation_margin_score),
            "earnings_revision_score": _normalize(extra.get("earnings_revision_score")),
            "crowd_neglect_score": _normalize(extra.get("crowd_neglect_score"), 100.0 - trend),
            "accumulation_flow_score": _normalize(extra.get("accumulation_flow_score")),
            "technical_bottom_score": _normalize(extra.get("technical_bottom_score"), 100.0 - trend),
        }

    def _multi_asset_components(self, score_input: StrategyScoreInput, extra: dict[str, Any]) -> dict[str, float]:
        trend = _normalize(score_input.trend_strength)
        return {
            "asset_trend_score": _normalize(extra.get("asset_trend_score"), trend),
            "volatility_adjusted_momentum": _normalize(extra.get("volatility_adjusted_momentum"), trend),
            "macro_liquidity_score": _normalize(extra.get("macro_liquidity_score")),
            "risk_score": _normalize(extra.get("risk_score")),
        }

    def _momentum_components(self, score_input: StrategyScoreInput, extra: dict[str, Any]) -> dict[str, float]:
        return {
            "liquidity_score": _normalize(score_input.liquidity_score, default=0.0),
            "trend_strength": _normalize(score_input.trend_strength),
        }

    @staticmethod
    def _new_high_score(ts_bucket: str | None, trend_strength: float) -> float:
        bucket_scores = {
            "S5": 95.0,
            "S4": 80.0,
            "S3": 55.0,
            "S2": 35.0,
            "S1": 15.0,
        }
        return bucket_scores.get((ts_bucket or "").upper(), trend_strength)
