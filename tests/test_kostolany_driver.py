"""kostolany_driver 단위 테스트 — 모드별 전략 매핑과 집계 로직."""

from __future__ import annotations

import datetime as dt

from maps.backtest.engine import BacktestResult, TradeRecord
from maps.backtest.kostolany_comparison import MODE_A, MODE_B, MODE_C, MODE_D
from maps.backtest.kostolany_driver import _aggregate, mode_strategy_ids


def _bt(cagr: float, mdd: float, sharpe: float, win_rate: float, trades: list[TradeRecord]) -> BacktestResult:
    return BacktestResult(
        strategy_id="x",
        start_date=dt.date(2020, 1, 1),
        end_date=dt.date(2020, 12, 31),
        initial_capital=1_000_000.0,
        final_value=1_100_000.0,
        cagr=cagr,
        mdd=mdd,
        sharpe=sharpe,
        win_rate=win_rate,
        total_trades=len(trades),
        trade_list=trades,
    )


def _trade(entry: dt.date, exit_: dt.date) -> TradeRecord:
    return TradeRecord(
        ticker="005930",
        entry_date=entry,
        exit_date=exit_,
        entry_price=100.0,
        exit_price=110.0,
        qty=10,
        gross_pnl=100.0,
        net_pnl=90.0,
        exit_reason="signal",
    )


def test_mode_a_and_c_exclude_contrarian() -> None:
    for mode in (MODE_A, MODE_C):
        ids = mode_strategy_ids(mode)
        assert "contrarian_quality_accumulation_v1" not in ids
        assert len(ids) == 7


def test_mode_b_and_d_include_contrarian() -> None:
    for mode in (MODE_B, MODE_D):
        ids = mode_strategy_ids(mode)
        assert "contrarian_quality_accumulation_v1" in ids
        assert len(ids) == 8


def test_aggregate_empty_returns_error_result() -> None:
    result = _aggregate("legacy", "crash", [])
    assert result.trade_count == 0
    assert result.error is not None
    assert result.cagr is None


def test_aggregate_ignores_zero_trade_results() -> None:
    # 거래 없는 결과는 평균에서 제외되어야 한다.
    zero = _bt(0.0, 0.0, 0.0, 0.0, [])
    one = _bt(0.20, -0.10, 1.5, 0.6, [_trade(dt.date(2020, 1, 2), dt.date(2020, 1, 12))])
    result = _aggregate("kostolany_full", "crash", [zero, one])
    assert result.trade_count == 1
    assert result.cagr == 0.20  # zero-trade 결과 제외 → one만 반영


def test_aggregate_averages_and_worst_mdd() -> None:
    r1 = _bt(0.10, -0.15, 1.0, 0.5, [_trade(dt.date(2020, 1, 2), dt.date(2020, 1, 7))])   # 5일
    r2 = _bt(0.30, -0.25, 2.0, 0.7, [_trade(dt.date(2020, 2, 1), dt.date(2020, 2, 16))])  # 15일
    result = _aggregate("kostolany_full", "crash", [r1, r2])
    assert abs(result.cagr - 0.20) < 1e-9       # (0.10+0.30)/2
    assert abs(result.sharpe - 1.5) < 1e-9
    assert result.mdd == -0.25                  # 최악(가장 깊은) MDD
    assert result.trade_count == 2
    assert abs(result.avg_hold_days - 10.0) < 1e-9  # (5+15)/2
    assert result.avg_cash_ratio is None
