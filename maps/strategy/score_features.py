"""Small deterministic feature set for fully measured strategy scores."""

from __future__ import annotations

import pandas as pd

from maps.strategy.base import StrategyType


def _clamp(value: float) -> float:
    """Clamp a derived score to 0-100."""
    return round(max(0.0, min(float(value), 100.0)), 2)


def strategy_extra_scores(
    frame: pd.DataFrame,
    strategy_type: StrategyType | str | None,
    *,
    supply_demand_score: float | None,
    macro_liquidity_score: float | None,
) -> dict[str, object]:
    """Derive only components supported by actual OHLCV/feed observations."""
    if frame.empty:
        return {}
    close = frame["close"].astype(float)
    volume = frame["volume"].astype(float)
    strategy = strategy_type.value if isinstance(strategy_type, StrategyType) else str(strategy_type or "")
    sources: dict[str, str] = {}
    extra: dict[str, object] = {}

    if strategy == StrategyType.PULLBACK.value and len(frame) >= 60:
        ma20 = float(close.tail(20).mean())
        ma60 = float(close.tail(60).mean())
        last = float(close.iloc[-1])
        recent_volume = float(volume.tail(5).mean())
        baseline_volume = float(volume.iloc[-25:-5].mean()) if len(volume) >= 25 else 0.0
        if ma20 > 0 and ma60 > 0 and baseline_volume > 0:
            extra.update(
                support_quality=_clamp(100.0 - abs(last / ma20 - 1.0) * 1000.0),
                volume_cooling=_clamp((2.0 - recent_volume / baseline_volume) * 100.0),
                trend_preservation=_clamp(
                    50.0 + (last / ma60 - 1.0) * 500.0 + (ma20 / ma60 - 1.0) * 500.0
                ),
                supply_demand_score=supply_demand_score,
            )
            sources.update(
                support_quality="ohlcv.ma20_distance",
                volume_cooling="ohlcv.volume_5d_vs_20d",
                trend_preservation="ohlcv.ma20_ma60",
            )
            if supply_demand_score is not None:
                sources["supply_demand_score"] = "pykrx.investor_flow_5d"

    elif strategy == StrategyType.BREAKOUT.value and len(frame) >= 252:
        prior_high = float(close.iloc[-252:-1].max())
        if prior_high > 0:
            extra["new_high_score"] = _clamp(float(close.iloc[-1]) / prior_high * 100.0)
            sources["new_high_score"] = "ohlcv.252d_high"
        extra["institutional_foreign_flow"] = supply_demand_score
        if supply_demand_score is not None:
            sources["institutional_foreign_flow"] = "pykrx.investor_flow_5d"

    elif strategy == StrategyType.MULTI_ASSET_TREND.value and len(frame) >= 60:
        returns = close.pct_change().dropna()
        vol = float(returns.tail(20).std()) if not returns.empty else 0.0
        momentum = float(close.iloc[-1] / close.iloc[-21] - 1.0) if len(close) >= 21 else 0.0
        extra.update(
            asset_trend_score=_clamp(50.0 + momentum * 500.0),
            volatility_adjusted_momentum=_clamp(50.0 + momentum / max(vol, 0.001) * 5.0),
            macro_liquidity_score=macro_liquidity_score,
            risk_score=_clamp(100.0 - vol * 1000.0),
        )
        sources.update(
            asset_trend_score="ohlcv.20d_momentum",
            volatility_adjusted_momentum="ohlcv.20d_momentum_volatility",
            risk_score="ohlcv.20d_volatility",
        )
        if macro_liquidity_score is not None:
            sources["macro_liquidity_score"] = "market.liquidity"

    extra["_sources"] = sources
    return extra
