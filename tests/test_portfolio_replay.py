"""다종목 포트폴리오 리플레이 엔진 테스트."""

from __future__ import annotations

import numpy as np
import pandas as pd

from maps.backtest.portfolio_replay import PortfolioConfig, PortfolioReplayEngine
from maps.strategy.base import BaseStrategy


class _ScriptedStrategy(BaseStrategy):
    """신호 컬럼이 미리 채워진 DataFrame을 그대로 반환하는 테스트용 전략."""

    strategy_id = "scripted_pf"
    strategy_group = "pullback_short"

    def generate_signals(self, data: pd.DataFrame, params: dict) -> pd.DataFrame:
        return data

    def param_grid(self) -> list[dict]:
        return [{}]

    @property
    def default_params(self) -> dict:
        return {}


def _series(n: int, start: float, step: float, entry_idx: int) -> pd.DataFrame:
    dates = pd.date_range("2020-01-02", periods=n, freq="B")
    close = np.array([start + step * i for i in range(n)], dtype=float)
    entry = [i == entry_idx for i in range(n)]
    return pd.DataFrame(
        {
            "open": close,           # open=close 로 단순화
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": [1_000_000] * n,
            "entry_signal": entry,
            "exit_signal": [False] * n,
            "stop_price": close * 0.5,   # 멀어서 트리거되지 않음
        },
        index=dates,
    )


def test_slot_constraint_limits_concurrent_positions() -> None:
    """동시 보유 슬롯이 1이면 동시 신호 2종목 중 1종목만 보유한다."""
    data = {"AAA": _series(30, 10_000, 50, entry_idx=2),
            "BBB": _series(30, 10_000, 50, entry_idx=2)}

    one = PortfolioReplayEngine(PortfolioConfig(max_positions=1)).run(_ScriptedStrategy(), {}, data)
    two = PortfolioReplayEngine(PortfolioConfig(max_positions=2)).run(_ScriptedStrategy(), {}, data)

    # 1슬롯: 보유자가 끝까지 슬롯을 점유 → 한 종목만 거래
    assert one.total_trades == 1
    # 2슬롯: 두 종목 모두 진입
    assert two.total_trades == 2


def test_same_bar_fill_inflates_vs_next_open() -> None:
    """상승장에서 동일봉 종가 체결(look-ahead)은 t+1 시가 체결보다 수익을 부풀린다."""
    data = {"AAA": _series(30, 10_000, 100, entry_idx=2)}

    next_open = PortfolioReplayEngine(
        PortfolioConfig(max_positions=1, fill_mode="next_open")
    ).run(_ScriptedStrategy(), {}, data)
    same_bar = PortfolioReplayEngine(
        PortfolioConfig(max_positions=1, fill_mode="same_bar_close")
    ).run(_ScriptedStrategy(), {}, data)

    # 동일봉 체결은 더 싼 신호일 종가로 진입 → 최종 자산이 더 높다(낙관 편향)
    assert same_bar.final_value > next_open.final_value


def test_cash_never_goes_negative() -> None:
    """현금 제약으로 보유 평가액이 음수 현금을 만들지 않는다."""
    data = {f"T{i}": _series(30, 10_000, 20, entry_idx=2) for i in range(8)}
    res = PortfolioReplayEngine(
        PortfolioConfig(initial_capital=5_000_000, max_positions=8)
    ).run(_ScriptedStrategy(), {}, data)

    assert all(v > 0 for v in res.equity_curve)
    assert res.final_value > 0
