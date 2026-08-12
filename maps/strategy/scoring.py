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
    component_sources: dict[str, str] | None = None
    missing_components: list[str] | None = None
    coverage_ratio: float = 1.0
    score_status: str = "complete"
    score_ready: bool = True


@dataclass(frozen=True)
class StrategyScoreInput:
    strategy_type: StrategyType | str | None
    liquidity_score: float | None
    trend_strength: float | None
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


def _measured_score(
    components: dict[str, float | None], weights: dict[str, float]
) -> tuple[float, dict[str, float], list[str], float]:
    """Return a renormalized observation score plus explicit coverage metadata."""
    measured = {
        name: _normalize(value)
        for name, value in components.items()
        if value is not None
    }
    missing = [name for name in weights if name not in measured]
    coverage = round(sum(weights[name] for name in measured), 4)
    if not measured or coverage <= 0:
        return 50.0, measured, missing, 0.0
    score = sum(measured[name] * weights[name] for name in measured) / coverage
    return round(score, 2), measured, missing, coverage


class LegacyFinalScoreCalculator:
    """Preserve the original MAPS ranking formula."""

    def calculate(
        self,
        *,
        factor_score: float | None,
        trend_strength: float | None,
        ai_technical_score: float | None = None,
        ai_weight: float = 0.0,
        strategy_type: StrategyType | str | None = None,
        ts_bucket: str | None = None,
    ) -> ScoreResult:
        base_score, components, missing, coverage = _measured_score(
            {"liquidity_score": factor_score, "trend_strength": trend_strength},
            {"liquidity_score": 0.60, "trend_strength": 0.40},
        )
        bucket = f"({ts_bucket})" if ts_bucket else ""
        reason = (
            f"legacy measured coverage={coverage:.2f}{bucket} = {round(base_score, 2):.2f}"
        )
        if ai_technical_score is not None:
            ai_score = _normalize(ai_technical_score)
            weight = max(0.0, min(float(ai_weight), 1.0))
            final_score = round((1.0 - weight) * base_score + weight * ai_score, 2)
            components["ai_technical_score"] = ai_score
            reason = (
                f"{reason}; AI 오버레이 {ai_score:.1f}"
                f"×{round(weight, 2)} → {final_score:.2f}"
            )
        else:
            final_score = round(base_score, 2)

        return ScoreResult(
            final_score=final_score,
            score_type=ScoreType.LEGACY.value,
            strategy_type=_strategy_type_value(strategy_type),
            component_scores=components,
            reason=reason,
            component_sources={
                name: source
                for name, source in {
                    "liquidity_score": "universe.turnover_liquidity",
                    "trend_strength": "indicator.trend_strength",
                }.items()
                if name in components
            },
            missing_components=missing,
            coverage_ratio=coverage,
            score_status="complete" if not missing else "partial",
            score_ready=coverage == 1.0,
        )


class StrategyAwareScoreCalculator:
    """Apply strategy-specific formulas without inventing missing inputs."""

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
            raw_components = self._breakout_components(score_input, extra)
            weights = self._BREAKOUT_WEIGHTS
            excluded_reason = None
            label = "breakout"
        elif strategy_type == StrategyType.PULLBACK.value:
            raw_components = self._pullback_components(score_input, extra)
            weights = self._PULLBACK_WEIGHTS
            excluded_reason = None
            label = "pullback"
        elif strategy_type == StrategyType.CONTRARIAN_QUALITY.value:
            raw_components = self._contrarian_components(score_input, extra)
            weights = self._CONTRARIAN_WEIGHTS
            raw_score, components, missing, coverage = _measured_score(raw_components, weights)
            excluded_reason = None
            if components.get("valuation_margin_score", 0.0) < 60.0:
                excluded_reason = "valuation_margin_below_contrarian_threshold"
                score = min(raw_score, 39.0)
            else:
                score = raw_score
            label = "contrarian_quality"
        elif strategy_type == StrategyType.MULTI_ASSET_TREND.value:
            raw_components = self._multi_asset_components(score_input, extra)
            weights = self._MULTI_ASSET_WEIGHTS
            excluded_reason = None
            label = "multi_asset_trend"
        else:
            raw_components = self._momentum_components(score_input, extra)
            weights = {"liquidity_score": 0.50, "trend_strength": 0.50}
            excluded_reason = None
            label = "momentum"

        if strategy_type != StrategyType.CONTRARIAN_QUALITY.value:
            score, components, missing, coverage = _measured_score(raw_components, weights)
        default_sources = {
            "liquidity_score": "universe.turnover_liquidity",
            "trend_strength": "indicator.trend_strength",
            "valuation_margin_score": "fundamentals.valuation_margin",
        }
        sources = {
            name: str(
                (extra.get("_sources") or {}).get(
                    name, default_sources.get(name, "derived")
                )
            )
            for name in components
        }
        status = "complete" if not missing else ("partial" if components else "unavailable")
        reason = self._build_reason(label, components, weights, score, missing, coverage)

        ai_score = _normalize(score_input.ai_technical_score) if score_input.ai_technical_score is not None else None
        if ai_score is not None and score_input.ai_weight > 0:
            weight = max(0.0, min(float(score_input.ai_weight), 1.0))
            score = round((1.0 - weight) * score + weight * ai_score, 2)
            components["ai_technical_score"] = ai_score
            reason = f"{reason}; AI 오버레이 {ai_score:.1f}×{round(weight, 2)}"

        return ScoreResult(
            final_score=round(score, 2),
            score_type=ScoreType.STRATEGY_AWARE.value,
            strategy_type=strategy_type,
            component_scores=components,
            reason=reason,
            excluded_reason=excluded_reason,
            component_sources=sources,
            missing_components=missing,
            coverage_ratio=coverage,
            score_status=status,
            score_ready=coverage == 1.0,
        )

    @classmethod
    def _build_reason(
        cls,
        label: str,
        components: dict[str, float],
        weights: dict[str, float],
        score: float,
        missing: list[str],
        coverage: float,
    ) -> str:
        """종목별 실측치로 근거 문자열을 만든다.

        placeholder 마커 형식(``; neutral placeholders=a,b,c``)은 SectorScorer 와
        동일한 규약 — 다이제스트가 이 접미사를 파싱해 "미측정" 목록을 노출한다.
        """
        parts = ", ".join(
            f"{name}={components[name]:.1f}×{weights[name]:.2f}"
            for name in weights
            if name in components
        )
        reason = f"{label}: {parts} = {score:.2f}"
        if missing:
            reason = (
                f"{reason}; partial coverage={coverage:.2f}; "
                f"missing={','.join(missing)}"
            )
        return reason

    def _breakout_components(self, score_input: StrategyScoreInput, extra: dict[str, Any]) -> dict[str, float | None]:
        return {
            "liquidity_score": score_input.liquidity_score,
            "trend_strength": score_input.trend_strength,
            "new_high_score": extra.get("new_high_score"),
            "institutional_foreign_flow": extra.get("institutional_foreign_flow"),
        }

    def _pullback_components(self, score_input: StrategyScoreInput, extra: dict[str, Any]) -> dict[str, float | None]:
        return {
            "support_quality": extra.get("support_quality"),
            "volume_cooling": extra.get("volume_cooling"),
            "trend_preservation": extra.get("trend_preservation"),
            "supply_demand_score": extra.get("supply_demand_score"),
            "valuation_margin_score": score_input.valuation_margin_score,
        }

    def _contrarian_components(self, score_input: StrategyScoreInput, extra: dict[str, Any]) -> dict[str, float | None]:
        return {
            "valuation_margin_score": score_input.valuation_margin_score,
            "earnings_revision_score": extra.get("earnings_revision_score"),
            "crowd_neglect_score": extra.get("crowd_neglect_score"),
            "accumulation_flow_score": extra.get("accumulation_flow_score"),
            "technical_bottom_score": extra.get("technical_bottom_score"),
        }

    def _multi_asset_components(self, score_input: StrategyScoreInput, extra: dict[str, Any]) -> dict[str, float | None]:
        return {
            "asset_trend_score": extra.get("asset_trend_score"),
            "volatility_adjusted_momentum": extra.get("volatility_adjusted_momentum"),
            "macro_liquidity_score": extra.get("macro_liquidity_score"),
            "risk_score": extra.get("risk_score"),
        }

    def _momentum_components(self, score_input: StrategyScoreInput, extra: dict[str, Any]) -> dict[str, float | None]:
        return {
            "liquidity_score": score_input.liquidity_score,
            "trend_strength": score_input.trend_strength,
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
