"""BacktestEngine + PullbackV3Strategy 단위 테스트 (Phase 2 Step 5)."""

from __future__ import annotations

import datetime

import numpy as np
import pandas as pd
import pytest

from maps.backtest.engine import BacktestEngine
from maps.strategy.multi_asset_trend_v1 import MultiAssetTrendV1Strategy
from maps.strategy.pullback_v3 import PullbackV3Strategy


def _make_ohlcv(n: int = 60, seed: int = 42) -> pd.DataFrame:
    """n일 치 가짜 OHLCV DataFrame을 생성한다 (date 인덱스)."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range(start="2020-01-02", periods=n, freq="B")
    close = 10_000 + np.cumsum(rng.normal(0, 100, n))
    close = np.maximum(close, 1_000)

    df = pd.DataFrame(
        {
            "open": close * rng.uniform(0.99, 1.01, n),
            "high": close * rng.uniform(1.00, 1.02, n),
            "low": close * rng.uniform(0.98, 1.00, n),
            "close": close,
            "volume": rng.integers(100_000, 1_000_000, n),
        },
        index=dates,
    )
    return df


# ---------------------------------------------------------------------------
# 1. 3조건 AND 신호 검증
# ---------------------------------------------------------------------------
def test_pullback_signals():
    """entry_signal은 MA5>MA_long AND RSI(2)<threshold AND low<prev_low 조건."""
    strategy = PullbackV3Strategy()
    df = _make_ohlcv(120)
    params = {"rsi_threshold": 15, "ma_long": 20}

    result = strategy.generate_signals(df, params)

    assert "entry_signal" in result.columns
    assert "exit_signal" in result.columns
    assert "stop_price" in result.columns

    # entry_signal은 bool dtype이어야 함
    assert result["entry_signal"].dtype == bool

    # 진입 당일 exit_signal은 False
    entry_days = result[result["entry_signal"]]
    assert (entry_days["exit_signal"] == False).all()

    # stop_price는 close * 0.95
    assert ((result["stop_price"] - result["close"] * 0.95).abs() < 1e-6).all()


# ---------------------------------------------------------------------------
# 2. 미래 데이터 참조 금지 (look-ahead 없음)
# ---------------------------------------------------------------------------
def test_no_future_leak():
    """신호 생성 시 미래 데이터를 참조하지 않는다.

    마지막 N행을 제거한 서브셋과 전체 데이터셋에서 공통 날짜의 신호가 동일해야 한다.
    """
    strategy = PullbackV3Strategy()
    df = _make_ohlcv(80)
    params = {"rsi_threshold": 10, "ma_long": 20}

    full = strategy.generate_signals(df.copy(), params)
    partial = strategy.generate_signals(df.iloc[:-10].copy(), params)

    common_idx = partial.index
    for col in ("entry_signal", "exit_signal", "stop_price"):
        diff = (full.loc[common_idx, col] != partial[col]).sum()
        assert diff == 0, f"look-ahead detected in '{col}': {diff} rows differ"


# ---------------------------------------------------------------------------
# 3. param_grid 조합 수 = 9
# ---------------------------------------------------------------------------
def test_param_grid():
    """param_grid()는 rsi_threshold 3 × ma_long 3 = 9개 조합을 반환한다."""
    strategy = PullbackV3Strategy()
    grid = strategy.param_grid()

    assert len(grid) == 9

    rsi_values = sorted({p["rsi_threshold"] for p in grid})
    ma_values = sorted({p["ma_long"] for p in grid})

    # v3.2: IS 과적합 방지를 위해 좁은 범위로 수정 (v3.0: [5,10,15]×[20,30,40])
    assert rsi_values == [8, 10, 12]
    assert ma_values == [18, 20, 22]


def test_strategy_required_bars_matches_warmup_window():
    strategy = MultiAssetTrendV1Strategy()

    assert strategy.required_bars(strategy.default_params) == 62
    assert strategy.required_bars({"ma_fast": 20, "ma_slow": 80}) == 82


# ---------------------------------------------------------------------------
# 4. BacktestResult에 trade_list 포함
# ---------------------------------------------------------------------------
def test_backtest_result_has_trade_list():
    """BacktestResult.trade_list 가 존재하고 MC 테스트용으로 사용 가능하다."""
    engine = BacktestEngine(initial_capital=100_000_000)
    strategy = PullbackV3Strategy()
    df = _make_ohlcv(120)
    params = strategy.default_params

    result = engine.run(strategy, params, df)

    assert hasattr(result, "trade_list")
    assert isinstance(result.trade_list, list)
    assert hasattr(result, "equity_curve")
    assert len(result.equity_curve) > 0
