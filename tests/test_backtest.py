"""BacktestEngine + PullbackV3Strategy 단위 테스트 (Phase 2 Step 5)."""

from __future__ import annotations

import datetime

import numpy as np
import pandas as pd
import pytest

from maps.backtest.engine import BacktestEngine
from maps.strategy.base import BaseStrategy
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

    # 레짐 필터(_REGIME_SHIFT=20) 추가로 required_bars = ma_slow + 20 + 2
    assert strategy.required_bars(strategy.default_params) == 82   # ma_slow=60 + 22
    assert strategy.required_bars({"ma_fast": 20, "ma_slow": 80}) == 102  # ma_slow=80 + 22


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


# ---------------------------------------------------------------------------
# 5. 체결 모델 (C-1): t+1 시가 체결 / 손절 갭 / 동일봉 재진입 금지
# ---------------------------------------------------------------------------
class _ScriptedStrategy(BaseStrategy):
    """신호 컬럼이 미리 채워진 DataFrame을 그대로 돌려주는 테스트용 전략.

    엔진의 체결 타이밍만 독립적으로 검증하기 위해 신호 생성을 우회한다.
    """

    strategy_id = "scripted_test"
    strategy_group = "pullback_short"

    def generate_signals(self, data: pd.DataFrame, params: dict) -> pd.DataFrame:
        return data

    def param_grid(self) -> list[dict]:
        return [{}]

    @property
    def default_params(self) -> dict:
        return {}


def _scripted_df(spec: dict, n: int) -> pd.DataFrame:
    """date 인덱스 + 명시적 신호 컬럼을 가진 OHLCV DataFrame을 만든다."""
    dates = pd.date_range(start="2020-01-02", periods=n, freq="B")
    base = {"volume": [1_000] * n}
    base.update(spec)
    return pd.DataFrame(base, index=dates)


def test_entry_fills_next_bar_open():
    """t일 종가 신호 → t+1일 시가로 체결된다 (동일봉 체결 아님)."""
    df = _scripted_df(
        {
            "open": [100, 100, 100, 110, 120, 120],
            "high": [101, 101, 101, 111, 121, 121],
            "low": [99, 99, 99, 109, 119, 119],
            "close": [100, 100, 100, 110, 120, 120],
            "entry_signal": [False, False, True, False, False, False],
            "exit_signal": [False] * 6,
            "stop_price": [95.0] * 6,
        },
        n=6,
    )
    engine = BacktestEngine(initial_capital=10_000_000)
    result = engine.run(_ScriptedStrategy(), {}, df)

    assert result.trade_list, "체결된 거래가 없습니다"
    trade = result.trade_list[0]
    # 신호는 index 2(종가 100)에서 발생, 체결은 index 3 시가 110
    assert trade.entry_price == 110.0
    assert trade.entry_date == df.index[3].date()


def test_stop_fills_with_gap():
    """갭하락으로 시가가 손절가보다 낮으면 손절가가 아닌 시가로 체결된다."""
    df = _scripted_df(
        {
            "open": [100, 100, 100, 100, 80, 80],   # index 4 갭하락 시가 80
            "high": [101, 101, 101, 101, 82, 82],
            "low": [99, 99, 99, 99, 78, 78],
            "close": [100, 100, 100, 100, 79, 79],
            "entry_signal": [False, False, True, False, False, False],
            "exit_signal": [False] * 6,
            "stop_price": [90.0] * 6,
        },
        n=6,
    )
    engine = BacktestEngine(initial_capital=10_000_000)
    result = engine.run(_ScriptedStrategy(), {}, df)

    assert result.trade_list
    trade = result.trade_list[0]
    assert trade.exit_reason == "stop_loss"
    # 손절가 90이 아니라 갭하락 시가 80에 체결되어야 보수적
    assert trade.exit_price == 80.0


def test_no_same_bar_reentry():
    """손절 청산이 일어난 봉에서는 같은 봉에 재진입하지 않는다."""
    df = _scripted_df(
        {
            "open": [100, 100, 100, 100, 80, 80, 80],
            "high": [101, 101, 101, 101, 82, 82, 82],
            "low": [99, 99, 99, 99, 78, 78, 78],
            "close": [100, 100, 100, 100, 79, 79, 79],
            # index 1 신호 → index 2 체결; index 3 신호 → index 4(손절봉) 체결 시도
            "entry_signal": [False, True, False, True, False, False, False],
            "exit_signal": [False] * 7,
            "stop_price": [90.0] * 7,
        },
        n=7,
    )
    engine = BacktestEngine(initial_capital=10_000_000)
    result = engine.run(_ScriptedStrategy(), {}, df)

    # 손절봉(index 4)에서 청산만 일어나고 재진입은 차단 → 거래 1건
    assert len(result.trade_list) == 1
    assert result.trade_list[0].exit_reason == "stop_loss"


# ---------------------------------------------------------------------------
# Sharpe 노출 가중 rf (2026-08-02 재설계, 이월 20번 회귀 방지)
# ---------------------------------------------------------------------------
def _sharpe_fixture(exposure: float, alpha_daily: float = 0.0001, noise: float = 0.0005):
    """노출 비중이 일정한 합성 계좌 곡선을 만든다.

    투자분은 rf + alpha ± noise 를 벌고 나머지는 현금(무수익).
    """
    rf_daily = 0.03 / 252.0
    n = 252
    equity = [100_000_000.0]
    for i in range(n):
        r_asset = rf_daily + alpha_daily + (noise if i % 2 == 0 else -noise)
        equity.append(equity[-1] * (1 + exposure * r_asset))
    dates = pd.date_range("2024-01-02", periods=n + 1, freq="B")
    data = pd.DataFrame({"close": 1.0}, index=dates)
    exposure_curve = [exposure] * (n + 1)
    return data, equity, exposure_curve


def test_sharpe_not_penalized_for_idle_cash():
    """저노출(10%) 전략이 유휴 현금 90%의 rf 기회비용에 얻어맞으면 안 된다.

    종전 공식(총자산 수익률 − rf 전체 차감)은 이 시나리오에서 샤프 ≈ −33으로
    왜곡됐다 — 운영 검증 8전략 전일 fail(sharpe_mean −2.9~−8.3)의 뿌리.
    투자분이 rf보다 alpha만큼 더 벌고 있으므로 샤프는 양수여야 한다.
    """
    engine = BacktestEngine()
    data, equity, exposure_curve = _sharpe_fixture(exposure=0.1)

    result = engine._compute_metrics("s", data, equity, [], exposure_curve)

    assert result.sharpe > 0, f"저노출 왜곡 재발: sharpe={result.sharpe}"


def test_sharpe_matches_legacy_formula_when_fully_invested():
    """완전 투자(노출 100%)면 종전 공식과 동일해야 한다 — 보정은 유휴 현금에만."""
    engine = BacktestEngine()
    data, equity, exposure_curve = _sharpe_fixture(exposure=1.0, alpha_daily=0.001)

    result = engine._compute_metrics("s", data, equity, [], exposure_curve)

    arr = np.array(equity)
    rets = np.diff(arr) / arr[:-1]
    legacy = (rets.mean() - 0.03 / 252.0) / (rets - 0.03 / 252.0).std() * np.sqrt(252)
    assert result.sharpe == pytest.approx(float(legacy), rel=1e-9)


def test_sharpe_exposure_scale_invariant():
    """같은 전략이면 노출 10%든 50%든 샤프가 (부호·규모 면에서) 같아야 한다."""
    engine = BacktestEngine()
    sharpes = []
    for exposure in (0.1, 0.5):
        data, equity, exposure_curve = _sharpe_fixture(exposure=exposure)
        sharpes.append(engine._compute_metrics("s", data, equity, [], exposure_curve).sharpe)

    assert sharpes[0] == pytest.approx(sharpes[1], rel=1e-6)
