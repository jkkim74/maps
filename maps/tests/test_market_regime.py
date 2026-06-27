from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from maps.market.regime import (  # noqa: E402
    BreadthLabel,
    CombinedWeeklyProvider,
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


def test_from_krx_falls_back_to_yfinance_when_pykrx_empty(monkeypatch) -> None:
    """pykrx 지수 데이터가 비면 KOSPI/KOSDAQ를 yfinance 폴백으로 받아온다."""
    provider = CombinedWeeklyProvider()
    monkeypatch.setattr(provider, "_krx_index_weekly", lambda name, n: [])
    captured: dict[str, object] = {}

    def fake_yf(ticker: str, n_weeks: int) -> list[float]:
        captured["ticker"] = ticker
        captured["n_weeks"] = n_weeks
        return [1.0, 2.0, 3.0, 4.0, 5.0]

    monkeypatch.setattr(provider, "_yf_weekly", fake_yf)

    assert provider._from_krx("KOSPI", 6) == [1.0, 2.0, 3.0, 4.0, 5.0]
    assert captured["ticker"] == "^KS11"
    assert captured["n_weeks"] == 6
    assert provider._from_krx("KOSDAQ", 6) == [1.0, 2.0, 3.0, 4.0, 5.0]
    assert captured["ticker"] == "^KQ11"


def test_from_krx_uses_pykrx_when_available(monkeypatch) -> None:
    """pykrx 지수 데이터가 있으면 yfinance 폴백을 호출하지 않는다."""
    provider = CombinedWeeklyProvider()
    monkeypatch.setattr(provider, "_krx_index_weekly", lambda name, n: [10.0, 11.0, 12.0])

    def boom(ticker: str, n_weeks: int) -> list[float]:
        raise AssertionError("yfinance fallback should not be called")

    monkeypatch.setattr(provider, "_yf_weekly", boom)

    assert provider._from_krx("KOSPI", 6) == [10.0, 11.0, 12.0]


class _FloorProvider:
    """KOSPI만 추세 방향을 제어하고 나머지 자산은 모두 하락시키는 테스트용 제공자."""

    def __init__(self, kospi_rising: bool) -> None:
        self._kospi_rising = kospi_rising

    def get_weekly_closes(self, asset_name: str, n_weeks: int) -> list[float]:
        if asset_name == "KOSPI" and self._kospi_rising:
            series = [100.0 + i for i in range(60)]
        else:
            series = [200.0 - i for i in range(60)]
        return series[-n_weeks:]


def test_kospi_floor_upgrades_weak_to_mixed() -> None:
    """KOSPI 상승추세 + weekly_trend pass면 weak가 mixed로 상향된다."""
    result = MarketRegimeAnalyzer(provider=_FloorProvider(kospi_rising=True)).analyze()

    assert result.assets[0].name == "KOSPI"
    assert result.assets[0].direction == "up"
    assert result.weekly_trend == WeeklyTrendLabel.PASS
    assert result.regime == RegimeLabel.MIXED


def test_kospi_floor_not_applied_when_kospi_down() -> None:
    """KOSPI가 하락추세면 하한선이 적용되지 않아 weak를 유지한다."""
    result = MarketRegimeAnalyzer(provider=_FloorProvider(kospi_rising=False)).analyze()

    assert result.assets[0].direction == "down"
    assert result.regime == RegimeLabel.WEAK


def test_kospi_floor_requires_weekly_trend_pass(monkeypatch) -> None:
    """KOSPI가 상승이라도 weekly_trend fail이면 하한선이 적용되지 않는다."""
    analyzer = MarketRegimeAnalyzer(provider=_FloorProvider(kospi_rising=True))
    monkeypatch.setattr(analyzer, "_check_weekly_trend", lambda: WeeklyTrendLabel.FAIL)

    result = analyzer.analyze()

    assert result.assets[0].direction == "up"
    assert result.weekly_trend == WeeklyTrendLabel.FAIL
    assert result.regime == RegimeLabel.WEAK


def _floored_mixed(breadth: BreadthLabel, *, floor_applied: bool = True) -> RegimeResult:
    """KOSPI 하한선으로 MIXED가 된 RegimeResult를 만든다."""
    return RegimeResult(
        regime=RegimeLabel.MIXED,
        weekly_trend=WeeklyTrendLabel.PASS,
        limit_ratio=0.0,
        kospi_ts=None,
        vol_regime=VolRegimeLabel.NORMAL,
        floor_applied=floor_applied,
        breadth=breadth,
    )


def test_breadth_guard_blocks_momentum_breakout_under_narrow_breadth() -> None:
    """floor + 좁은 장: 모멘텀·돌파·추세추종은 차단된다."""
    result = _floored_mixed(BreadthLabel.WEAK)

    for st in (
        StrategyEntryType.MOMENTUM,
        StrategyEntryType.BREAKOUT,
        StrategyEntryType.MULTI_ASSET_TREND,
    ):
        policy = result.entry_policy_for_strategy(st)
        assert policy.allowed is False
        assert policy.reason == "narrow_breadth_blocks_momentum_breakout"


def test_breadth_guard_allows_defensive_strategies_under_narrow_breadth() -> None:
    """floor + 좁은 장: 방어적 PULLBACK·CONTRARIAN_QUALITY는 통과한다."""
    result = _floored_mixed(BreadthLabel.WEAK)

    pullback = result.entry_policy_for_strategy(StrategyEntryType.PULLBACK)
    assert pullback.allowed is True

    contrarian = result.entry_policy_for_strategy(
        StrategyEntryType.CONTRARIAN_QUALITY, contrarian_enabled=True
    )
    assert contrarian.allowed is True


def test_breadth_guard_inactive_when_breadth_not_weak() -> None:
    """breadth가 WEAK가 아니면(STRONG/NORMAL/UNKNOWN) 가드 미적용."""
    for breadth in (BreadthLabel.STRONG, BreadthLabel.NORMAL, BreadthLabel.UNKNOWN):
        result = _floored_mixed(breadth)
        policy = result.entry_policy_for_strategy(StrategyEntryType.MOMENTUM)
        assert policy.allowed is True, breadth


def test_breadth_guard_inactive_without_floor() -> None:
    """floor가 아닌 실제 MIXED는 breadth WEAK여도 제한하지 않는다."""
    result = _floored_mixed(BreadthLabel.WEAK, floor_applied=False)
    policy = result.entry_policy_for_strategy(StrategyEntryType.MOMENTUM)
    assert policy.allowed is True


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
