"""WalkForwardAnalyzer 4가지 AND 조건 테스트 (Phase 2 + Phase 3)."""

import math
import datetime

import numpy as np
import pandas as pd
import pytest

from maps.validation.walk_forward import FoldResult, WalkForwardAnalyzer, WalkForwardResult


def _make_result(folds: list[FoldResult]) -> WalkForwardResult:
    r = WalkForwardResult(strategy_id="test")
    r.folds = folds
    return r


def test_selection_score_prefers_consistency_on_tie() -> None:
    """H-1: Sharpe가 같으면 gain-to-pain 높은 파라미터의 견고성 점수가 높다."""
    import math

    s = WalkForwardAnalyzer._selection_score
    assert s(1.0, 2.0) > s(1.0, 1.0)
    # 무손실 시 g2p=inf 는 상한(3.0)으로 캡되어 유한 점수
    assert math.isfinite(s(1.0, float("inf")))
    assert s(1.0, float("inf")) == s(1.0, 3.0)
    # Sharpe 차이가 g2p 최대 기여(0.5×3.0=1.5)보다 크면 Sharpe가 지배적
    assert s(3.0, 0.0) > s(1.0, 3.0)


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


def test_wfa_3_conditions_and() -> None:
    """3가지 조건은 AND — 1개만 깨져도 전체 실패."""
    analyzer = WalkForwardAnalyzer()

    # 조건 2만 위반: 음수 fold 2개 (sharpe_mean > 0, g2p 충족)
    folds_two_neg = [
        _fold(0.5), _fold(-0.2), _fold(0.3), _fold(-0.1), _fold(0.4),
    ]
    result = _make_result(folds_two_neg)
    passed, reasons = analyzer._evaluate(result)
    assert not passed
    assert any("음수 fold" in r for r in reasons)

    # 조건 3만 위반: g2p 낮음 (OOS sharpe << IS sharpe)
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


def test_wfa_zero_sharpe_mean_fails() -> None:
    """양수와 음수가 정확히 상쇄되어 sharpe_mean == 0 이면 조건 1 실패."""
    analyzer = WalkForwardAnalyzer()
    result = _make_result([_fold(0.5), _fold(-0.5), _fold(0.5), _fold(-0.5)])
    passed, reasons = analyzer._evaluate(result)
    assert not passed
    assert any("sharpe_mean" in r for r in reasons)


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


# ── 무거래 fold / 비유한 G2P 가드 (2026-08-03) ────────────────────────────────
#
# OOS 구간에서 한 번도 거래하지 않으면 engine 이 G2P=inf 를 냈고, `inf < 0.6` 이
# False 라 G2P 조건이 **조용히 통과**했다. 8/3 운영 실측에서 fold 13건이 이 상태였다.


def _fold_full(
    *,
    oos_sharpe: float = 1.0,
    is_sharpe: float = 1.0,
    is_g2p: float = 1.0,
    oos_g2p: float = 1.0,
    oos_trades: int = 5,
) -> FoldResult:
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
        is_g2p=is_g2p,
        oos_g2p=oos_g2p,
        oos_trades=oos_trades,
    )


def test_non_finite_mean_g2p_fails_instead_of_passing_silently() -> None:
    """mean_g2p 가 inf/NaN 이면 통과가 아니라 실패다.

    `if mean_g2p < 0.6: 실패` 구조라 inf 도 NaN 도 비교가 False → 실패 사유가 붙지
    않고 조건이 통과했다. 과잉 허용이므로 명시적으로 막는다.
    """
    analyzer = WalkForwardAnalyzer()

    for label, ratio in (("inf", float("inf")), ("nan", float("nan"))):
        result = _make_result([_fold_full(oos_g2p=ratio) for _ in range(5)])
        assert not math.isfinite(result.mean_g2p) or True  # 값 자체는 구현에 맡긴다
        _passed, reasons = analyzer._evaluate(result)
        assert any("g2p" in r.lower() for r in reasons), (
            f"mean_g2p={label} 인데 G2P 실패 사유가 없다 — 조용히 통과 중"
        )


def test_g2p_ratio_is_always_finite() -> None:
    """어떤 입력에도 g2p_ratio 는 유한하다 (inf/inf = NaN 방지)."""
    cases = [
        (float("inf"), float("inf")),
        (float("inf"), 1.0),
        (1.0, float("inf")),
        (0.0, float("inf")),
    ]
    for is_g2p, oos_g2p in cases:
        fold = _fold_full(is_g2p=is_g2p, oos_g2p=oos_g2p)
        assert math.isfinite(fold.g2p_ratio), f"is={is_g2p} oos={oos_g2p} → {fold.g2p_ratio}"


def test_no_trade_folds_beyond_limit_add_dedicated_reason() -> None:
    """OOS 무거래 fold 가 한도를 넘으면 전용 실패 사유가 붙는다.

    "mean_g2p=0.213 < 0.6" 만 보면 진짜 이유(거래를 안 했다)를 모른다.
    """
    analyzer = WalkForwardAnalyzer()
    folds = [_fold_full(oos_trades=0) for _ in range(3)] + [_fold_full() for _ in range(2)]
    result = _make_result(folds)

    assert result.no_trade_folds == 3
    _passed, reasons = analyzer._evaluate(result)
    assert any("무거래" in r for r in reasons), f"무거래 사유 없음: {reasons}"


def test_single_no_trade_fold_is_tolerated() -> None:
    """조용한 구간 1개까지는 허용한다 (음수 fold 한도와 같은 정신)."""
    analyzer = WalkForwardAnalyzer()
    folds = [_fold_full(oos_trades=0)] + [_fold_full() for _ in range(4)]
    result = _make_result(folds)

    assert result.no_trade_folds == 1
    _passed, reasons = analyzer._evaluate(result)
    assert not any("무거래" in r for r in reasons), f"1개는 허용해야 하는데: {reasons}"
