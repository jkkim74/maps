"""WalkForwardAnalyzer 4가지 AND 조건 테스트 (Phase 2 + Phase 3)."""

import datetime

import numpy as np
import pandas as pd
import pytest

from maps.validation.walk_forward import FoldResult, WalkForwardAnalyzer, WalkForwardResult


def _make_result(folds: list[FoldResult]) -> WalkForwardResult:
    r = WalkForwardResult(strategy_id="test")
    r.folds = folds
    return r


def _fold(oos_sharpe: float, is_sharpe: float = 1.0) -> FoldResult:
    return FoldResult(
        fold_idx=0,
        is_start=datetime.date(2022, 1, 1),
        is_end=datetime.date(2022, 12, 31),
        oos_start=datetime.date(2023, 1, 1),
        oos_end=datetime.date(2023, 3, 31),
        is_sharpe=is_sharpe,
        oos_sharpe=oos_sharpe,
        is_return=0.1,
        oos_return=0.05,
    )


# ---------------------------------------------------------------------------
# Phase 2 기존 테스트
# ---------------------------------------------------------------------------

def test_negative_sharpe_mean_fails() -> None:
    """조건 1: sharpe_mean <= 0 이면 실패."""
    analyzer = WalkForwardAnalyzer()
    result = _make_result([_fold(-0.5), _fold(-0.3), _fold(-0.2), _fold(0.0)])
    passed, reasons = analyzer._evaluate(result)
    assert not passed
    assert any("sharpe_mean" in r for r in reasons)


def test_too_many_negative_folds_fails() -> None:
    """조건 3: 음수 fold > 1이면 실패."""
    analyzer = WalkForwardAnalyzer()
    result = _make_result([_fold(0.5), _fold(-0.1), _fold(-0.2), _fold(0.3)])
    passed, reasons = analyzer._evaluate(result)
    assert not passed
    assert any("음수 fold" in r for r in reasons)


def test_all_conditions_pass() -> None:
    """4가지 조건 모두 충족 시 통과."""
    analyzer = WalkForwardAnalyzer()
    result = _make_result(
        [_fold(1.2, 1.5), _fold(0.9, 1.3), _fold(1.1, 1.4), _fold(0.8, 1.2)]
    )
    passed, reasons = analyzer._evaluate(result)
    assert passed
    assert reasons == []


# ---------------------------------------------------------------------------
# Phase 3 신규 테스트
# ---------------------------------------------------------------------------

def test_wfa_negative_mean_blocked() -> None:
    """모든 fold가 -0.3이면 sharpe_mean < 0 → 실패."""
    analyzer = WalkForwardAnalyzer()
    result = _make_result([_fold(-0.3)] * 5)
    passed, reasons = analyzer._evaluate(result)
    assert not passed
    assert any("sharpe_mean" in r for r in reasons)


def test_wfa_4_conditions_and() -> None:
    """4가지 조건은 AND — 1개만 깨져도 전체 실패."""
    analyzer = WalkForwardAnalyzer()

    # 조건 2만 위반: sharpe_mean > 0이지만 변동성 과대
    folds_high_cv = [
        _fold(3.0, 1.5),   # g2p_ratio=2.0
        _fold(0.01, 1.5),  # 극단 차이로 cv 폭등
        _fold(3.0, 1.5),
        _fold(0.01, 1.5),
    ]
    result = _make_result(folds_high_cv)
    passed, reasons = analyzer._evaluate(result)
    # sharpe_mean > 0 이나 cv가 크면 실패
    if not passed:
        assert len(reasons) >= 1
    # 이 조합이 통과하거나 실패 모두 가능하지만, 특정 조건 위반 → fail
    # 여기서는 "g2p < 0.6"으로도 실패할 수 있으므로 assert not passed
    assert not passed

    # 조건 4만 위반: g2p 낮음 (OOS sharpe << IS sharpe)
    folds_low_g2p = [
        _fold(0.3, 3.0),  # g2p_ratio = 0.3/3.0 = 0.1 < 0.6
        _fold(0.3, 3.0),
        _fold(0.3, 3.0),
        _fold(0.4, 3.0),
    ]
    result2 = _make_result(folds_low_g2p)
    passed2, reasons2 = analyzer._evaluate(result2)
    assert not passed2
    assert any("g2p" in r for r in reasons2)


def test_wfa_zero_division() -> None:
    """sharpe_mean == 0 이면 cv = inf → std/|mean| 조건 실패."""
    analyzer = WalkForwardAnalyzer()
    # 양수와 음수가 정확히 상쇄되어 mean=0
    result = _make_result([_fold(0.5), _fold(-0.5), _fold(0.5), _fold(-0.5)])
    passed, reasons = analyzer._evaluate(result)
    assert not passed
    # mean=0 → cv=inf, 그리고 sharpe_mean<=0 도 실패
    assert any("sharpe_mean" in r or "std/|mean|" in r for r in reasons)


def test_wfa_run_with_real_data() -> None:
    """run()이 실제 데이터로 WalkForwardResult 를 반환한다."""
    from maps.strategy.pullback_v3 import PullbackV3Strategy

    rng = np.random.default_rng(0)
    n = 2500  # ~10년
    dates = pd.date_range("2014-01-02", periods=n, freq="B")
    close = 10_000 + np.cumsum(rng.normal(0, 80, n))
    close = np.maximum(close, 1_000)
    df = pd.DataFrame(
        {
            "open": close * 0.998,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": rng.integers(int(1e6), int(1e7), n),
        },
        index=dates,
    )

    strategy = PullbackV3Strategy()
    analyzer = WalkForwardAnalyzer(is_months=24, oos_months=6, n_folds=3)
    result = analyzer.run(strategy, df, strategy.param_grid())

    assert result.strategy_id == "pullback_v3"
    assert len(result.folds) == 3
    # passed/fail 여부는 데이터에 따라 다름 — 구조만 확인
    assert isinstance(result.passed, bool)
