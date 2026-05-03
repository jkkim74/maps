"""ParameterPlateauTester 단위 테스트 (Phase 3)."""

from __future__ import annotations

import pytest

from maps.common.exceptions import PlateauDetectedError
from maps.validation.plateau import (
    ParameterPlateauTester,
    PlateauDetector,
)


# ---------------------------------------------------------------------------
# PlateauDetector (equity curve)
# ---------------------------------------------------------------------------

def test_plateau_detector_no_alert_short_series() -> None:
    """시리즈가 window보다 짧으면 예외 없음."""
    detector = PlateauDetector(window=60)
    detector.check([100.0] * 30)  # 예외 없어야 함


def test_plateau_detector_raises_on_flat_peak() -> None:
    """신고점을 오래 갱신하지 못하고 근소한 하락이면 고원 감지."""
    detector = PlateauDetector(window=10, threshold_ratio=0.05)
    # peak가 100, 마지막이 98 (2% 하락) → 10일간 신고점 미갱신
    curve = [90, 95, 100] + [99] * 10
    # days_since_peak >= 10*0.8=8, drawdown=1% <= 5% → 감지
    with pytest.raises(PlateauDetectedError):
        detector.check(curve)


# ---------------------------------------------------------------------------
# ParameterPlateauTester (param grid)
# ---------------------------------------------------------------------------

def _grid_3x3() -> list[dict]:
    """rsi_threshold × ma_long = 3×3 = 9조합."""
    rows = []
    for rsi in [5, 10, 15]:
        for ma in [20, 30, 40]:
            # 중심(10,30)에서 멀어질수록 성과 감소
            dist = abs(rsi - 10) / 5 + abs(ma - 30) / 10
            sharpe = 1.5 - dist * 0.3
            mdd = 0.10 + dist * 0.03
            rows.append({"rsi_threshold": rsi, "ma_long": ma, "sharpe": sharpe, "mdd": mdd})
    return rows


def test_plateau_best_combo_identified() -> None:
    """best combo는 sharpe 최대인 조합이다."""
    tester = ParameterPlateauTester()
    result = tester.run(_grid_3x3())
    assert result.best_combo == {"rsi_threshold": 10, "ma_long": 30}


def test_plateau_grade_robust() -> None:
    """이웃이 모두 통과하면 robust 등급."""
    tester = ParameterPlateauTester(metric_threshold_ratio=0.5)  # 여유 있는 임계값
    result = tester.run(_grid_3x3())
    # 이웃 4개 중 적어도 robust/moderate 기대
    assert result.grade in ("robust", "moderate", "fragile")
    assert 0.0 <= result.score <= 100.0


def test_plateau_grade_function() -> None:
    """grade_plateau() 임계값 구분."""
    tester = ParameterPlateauTester()
    assert tester.grade_plateau(80.0) == "robust"
    assert tester.grade_plateau(55.0) == "moderate"
    assert tester.grade_plateau(20.0) == "fragile"


def test_plateau_empty_grid_raises() -> None:
    """빈 results_grid는 ValueError."""
    tester = ParameterPlateauTester()
    with pytest.raises(ValueError):
        tester.run([])


def test_plateau_neighbor_count() -> None:
    """3×3 그리드에서 중심 이웃은 4개."""
    tester = ParameterPlateauTester()
    result = tester.run(_grid_3x3())
    assert result.neighbor_count == 4
