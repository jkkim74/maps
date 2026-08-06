"""Pullback V3.3 상태 기반 청산과 연구 격리 테스트."""

from __future__ import annotations

import pandas as pd
import pytest

from maps.backtest.engine import BacktestEngine
from maps.backtest.portfolio_replay import PortfolioConfig, PortfolioReplayEngine
from maps.backtest.position_exit import evaluate_position_exit
from maps.strategy.base import BaseStrategy, PositionExitPolicy, StrategyType
from maps.strategy.pullback_v3 import PullbackV3Strategy
from maps.strategy.pullback_v3_3 import EXIT_RESEARCH_GRID, PullbackV33Strategy
from scripts.evaluate_pullback_v3_3 import _window_passed


_POLICY = PositionExitPolicy(
    target_r=2.0,
    trailing_activate_r=1.5,
    trailing_distance_r=0.5,
)


class _ScriptedRStrategy(BaseStrategy):
    strategy_id = "test_stateful_r"
    strategy_group = "pullback_short"
    strategy_type = StrategyType.PULLBACK

    def generate_signals(self, data: pd.DataFrame, params: dict) -> pd.DataFrame:
        df = data.copy()
        df["entry_signal"] = False
        df.iloc[0, df.columns.get_loc("entry_signal")] = True
        df["exit_signal"] = False
        df["stop_price"] = 95.0
        return df

    def position_exit_policy(self, params: dict) -> PositionExitPolicy:
        return _POLICY

    def param_grid(self) -> list[dict]:
        return [{}]

    @property
    def default_params(self) -> dict:
        return {}


def _bars(*, target: bool = False, collide: bool = False) -> pd.DataFrame:
    dates = pd.date_range("2026-01-02", periods=5, freq="B")
    high3 = 111.0 if target or collide else 108.0
    low3 = 94.0 if collide else 99.0
    return pd.DataFrame(
        {
            "open": [100.0, 100.0, 101.0, 106.0, 106.0],
            "high": [101.0, 102.0, high3, 109.0, 107.0],
            "low": [99.0, 99.0, low3, 105.0, 104.0],
            "close": [100.0, 101.0, 107.0, 106.0, 105.0],
            "volume": [1_000_000] * 5,
        },
        index=dates,
    )


def test_v33_keeps_v32_entry_signal_exactly() -> None:
    dates = pd.date_range("2020-01-02", periods=100, freq="B")
    close = pd.Series([100 + i * 0.2 + (-3 if i % 11 == 0 else 0) for i in range(100)])
    frame = pd.DataFrame(
        {
            "open": close,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "volume": 100_000,
        },
        index=dates,
    )
    params = {"rsi_threshold": 10, "ma_long": 20}

    legacy = PullbackV3Strategy().generate_signals(frame, params)
    candidate = PullbackV33Strategy().generate_signals(frame, params)

    pd.testing.assert_series_equal(legacy["entry_signal"], candidate["entry_signal"])
    pd.testing.assert_series_equal(legacy["stop_price"], candidate["stop_price"])


def test_v33_policy_and_research_grid_are_fixed() -> None:
    strategy = PullbackV33Strategy()
    assert strategy.position_exit_policy(strategy.default_params) == _POLICY
    assert len(EXIT_RESEARCH_GRID) == 6
    assert len(strategy.param_grid()) == 9
    assert all(row["target_r"] == 2.0 for row in strategy.param_grid())


def test_research_acceptance_requires_payoff_sample_and_baseline_improvement() -> None:
    passing = {"trades": 30, "payoff_ratio": 1.3, "sharpe": 0.4, "mdd": -0.1}
    assert _window_passed(passing, baseline_sharpe=0.3) is True
    assert _window_passed({**passing, "payoff_ratio": 1.29}, 0.3) is False
    assert _window_passed({**passing, "trades": 29}, 0.3) is False
    assert _window_passed({**passing, "sharpe": 0.29}, 0.3) is False


def test_stateful_exit_uses_previous_hwm_for_trailing() -> None:
    decision = evaluate_position_exit(
        _POLICY,
        bar_open=106.0,
        bar_high=109.0,
        bar_low=105.0,
        bar_close=106.0,
        entry_price=100.0,
        stop_price=95.0,
        initial_risk=5.0,
        prior_high_water_mark=108.0,
        strategy_exit=False,
        next_open=True,
        is_last=False,
    )
    assert decision is not None
    assert decision.reason == "trailing_stop"
    assert decision.price == pytest.approx(105.5)


def test_same_bar_stop_wins_over_take_profit() -> None:
    result = BacktestEngine().run(_ScriptedRStrategy(), {}, _bars(target=True, collide=True))
    assert result.trade_list[0].exit_reason == "stop_loss"
    assert result.trade_list[0].exit_price == 95.0


def test_single_and_portfolio_engines_match_target_exit() -> None:
    frame = _bars(target=True)
    strategy = _ScriptedRStrategy()

    single = BacktestEngine().run(strategy, {}, frame)
    portfolio = PortfolioReplayEngine(PortfolioConfig(max_positions=1)).run(
        strategy, {}, {"TEST": frame}
    )

    assert single.trade_list[0].exit_reason == "take_profit"
    assert portfolio.trade_list[0].exit_reason == "take_profit"
    assert single.trade_list[0].exit_price == portfolio.trade_list[0].exit_price == 110.0
    assert single.trade_list[0].holding_days == portfolio.trade_list[0].holding_days


def test_v33_is_available_for_manual_research_but_not_scheduler() -> None:
    from maps.api.backtest import RUNNABLE_STRATEGIES as BACKTEST_STRATEGIES
    from maps.api.wfa import RUNNABLE_STRATEGIES as WFA_STRATEGIES
    from maps.ops.scheduler import _RUNNABLE_STRATEGIES as SCHEDULED_STRATEGIES

    assert "pullback_v3_3" in BACKTEST_STRATEGIES
    assert "pullback_v3_3" in WFA_STRATEGIES
    assert "pullback_v3_3" not in SCHEDULED_STRATEGIES
