from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from maps.market.regime import (  # noqa: E402
    MarketRegimeAnalyzer,
    MarketRegimeCompositeScorer,
    MarketRegimeInput,
    RegimeResult,
    RegimeLabel,
    StrategyEntryType,
    VolRegimeLabel,
    WeeklyTrendLabel,
)


def test_composite_scorer_returns_neutral_when_optional_data_missing() -> None:
    result = MarketRegimeCompositeScorer().score(
        MarketRegimeInput(
            legacy_regime=RegimeLabel.MIXED.value,
            vol_regime="normal",
            weekly_trend="pass",
        )
    )

    assert result.price_trend_score == 50.0
    assert result.volatility_score == 50.0
    assert result.liquidity_score == 50.0
    assert result.foreign_fx_score == 50.0
    assert result.psychology_score == 50.0
    assert result.final_market_score == 50.0
    assert result.composite_regime == RegimeLabel.MIXED.value


def test_strong_legacy_regime_is_downgraded_when_liquidity_is_bad() -> None:
    result = MarketRegimeCompositeScorer().score(
        MarketRegimeInput(
            legacy_regime=RegimeLabel.STRONG.value,
            vol_regime="low",
            weekly_trend="pass",
            price_trend_score=90,
            volatility_score=75,
            policy_rate_direction=0,
            yield_curve_change=0,
            usdkrw_stability=0,
            foreign_net_buying=0,
            margin_debt_growth=100,
            customer_deposit_change=0,
            credit_spread_risk=100,
        )
    )

    assert result.liquidity_score == 0.0
    assert result.composite_regime == RegimeLabel.MIXED.value
    assert "downgraded" in result.reason


def test_weak_legacy_regime_with_improving_liquidity_becomes_contrarian() -> None:
    result = MarketRegimeCompositeScorer().score(
        MarketRegimeInput(
            legacy_regime=RegimeLabel.WEAK.value,
            vol_regime="normal",
            weekly_trend="pass",
            price_trend_score=20,
            volatility_score=50,
            policy_rate_direction=80,
            yield_curve_change=80,
            usdkrw_stability=80,
            foreign_net_buying=80,
            margin_debt_growth=20,
            customer_deposit_change=80,
            credit_spread_risk=20,
        )
    )

    assert result.liquidity_score == 80.0
    assert result.composite_regime == "contrarian"
    assert "improving liquidity" in result.reason


def test_legacy_mode_off_keeps_existing_regime_result_shape() -> None:
    result = MarketRegimeAnalyzer(provider=None, kostolany_enabled=False).analyze()

    assert result.regime == RegimeLabel.MIXED
    assert result.composite is None
    assert result.entry_limit_ratio == 0.5


def test_weak_high_blocks_breakout_strategy() -> None:
    result = RegimeResult(
        regime=RegimeLabel.WEAK,
        weekly_trend=WeeklyTrendLabel.PASS,
        limit_ratio=0.0,
        kospi_ts=None,
        vol_regime=VolRegimeLabel.HIGH,
    )

    policy = result.entry_policy_for_strategy(
        StrategyEntryType.BREAKOUT,
        contrarian_enabled=True,
        contrarian_entry_limit_ratio=0.25,
    )

    assert policy.allowed is False
    assert policy.entry_limit_ratio == 0.0
    assert policy.reason == "weak_high_vol_blocks_trend_momentum_breakout_trading"


def test_weak_high_blocks_pullback_strategy() -> None:
    result = RegimeResult(
        regime=RegimeLabel.WEAK,
        weekly_trend=WeeklyTrendLabel.PASS,
        limit_ratio=0.0,
        kospi_ts=None,
        vol_regime=VolRegimeLabel.HIGH,
    )

    policy = result.entry_policy_for_strategy(
        StrategyEntryType.PULLBACK,
        contrarian_enabled=True,
        contrarian_entry_limit_ratio=0.25,
    )

    assert policy.allowed is False
    assert policy.entry_limit_ratio == 0.0


def test_weak_high_allows_contrarian_quality_with_limited_ratio() -> None:
    result = RegimeResult(
        regime=RegimeLabel.WEAK,
        weekly_trend=WeeklyTrendLabel.PASS,
        limit_ratio=0.0,
        kospi_ts=None,
        vol_regime=VolRegimeLabel.HIGH,
    )

    policy = result.entry_policy_for_strategy(
        StrategyEntryType.CONTRARIAN_QUALITY,
        contrarian_enabled=True,
        contrarian_entry_limit_ratio=0.25,
    )

    assert policy.allowed is True
    assert policy.entry_limit_ratio == 0.25
    assert policy.reason == "weak_high_vol_contrarian_quality_limited"


def test_strong_allows_breakout_with_legacy_limit_ratio() -> None:
    result = RegimeResult(
        regime=RegimeLabel.STRONG,
        weekly_trend=WeeklyTrendLabel.PASS,
        limit_ratio=0.0,
        kospi_ts=None,
        vol_regime=VolRegimeLabel.NORMAL,
    )

    policy = result.entry_policy_for_strategy(
        StrategyEntryType.BREAKOUT,
        contrarian_enabled=False,
        contrarian_entry_limit_ratio=0.25,
    )

    assert policy.allowed is True
    assert policy.entry_limit_ratio == 1.0
    assert policy.reason == "strong_market_allows_trend_breakout_pullback"
