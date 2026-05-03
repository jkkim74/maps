"""수익 고원(Plateau) 감지기 + 파라미터 고원 검사기."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from maps.common.exceptions import PlateauDetectedError


# ---------------------------------------------------------------------------
# Equity Curve 고원 감지기 (기존)
# ---------------------------------------------------------------------------

class PlateauDetector:
    """Equity Curve에서 수익 고원(정체 구간)을 감지한다.

    연속으로 신고점을 갱신하지 못하는 기간이 임계값 이상이면
    PlateauDetectedError를 발생시킨다.
    """

    def __init__(
        self,
        window: int = 60,
        threshold_ratio: float = 0.03,
    ) -> None:
        """
        Args:
            window: 고원 판단 기간 (영업일 수).
            threshold_ratio: 신고점 대비 최대 하락 허용 비율.
        """
        self._window = window
        self._threshold_ratio = threshold_ratio

    def check(self, equity_curve: list[float]) -> None:
        """고원 여부를 검사한다.

        Args:
            equity_curve: 일별 포트폴리오 가치 목록.

        Raises:
            PlateauDetectedError: 고원 감지 시.
        """
        if len(equity_curve) < self._window:
            return

        arr = np.array(equity_curve, dtype=float)
        recent = arr[-self._window:]
        peak = recent.max()
        last = recent[-1]

        drawdown_from_peak = (peak - last) / peak if peak > 0 else 0.0
        days_since_peak = int(self._window - 1 - np.argmax(recent[::-1]))

        if days_since_peak >= self._window * 0.8 and drawdown_from_peak <= self._threshold_ratio:
            raise PlateauDetectedError(
                f"수익 고원 감지: {days_since_peak}일간 신고점 미갱신 "
                f"(최고점 대비 {drawdown_from_peak:.2%} 하락)"
            )


# ---------------------------------------------------------------------------
# 파라미터 고원 검사기
# ---------------------------------------------------------------------------

@dataclass
class PlateauResult:
    """파라미터 고원 검사 결과."""

    best_combo: dict
    best_metric: float
    score: float            # 0~100: 이웃 파라미터 중 통과 비율
    grade: str              # "robust" | "moderate" | "fragile"
    passed: bool
    neighbor_count: int
    passing_neighbors: int


class ParameterPlateauTester:
    """파라미터 그리드에서 최선 조합의 이웃 안정성을 검사한다.

    이웃 정의: 파라미터 인덱스 기준 Manhattan distance == 1.
    통과 조건: metric >= best×0.75 AND |mdd| <= |best_mdd|×1.25.
    """

    def __init__(
        self,
        metric_threshold_ratio: float = 0.75,
        mdd_threshold_ratio: float = 1.25,
        robust_threshold: float = 70.0,
        moderate_threshold: float = 40.0,
    ) -> None:
        self._metric_ratio = metric_threshold_ratio
        self._mdd_ratio = mdd_threshold_ratio
        self._robust = robust_threshold
        self._moderate = moderate_threshold

    def run(
        self,
        results_grid: list[dict],
        metric_key: str = "sharpe",
        mdd_key: str = "mdd",
        param_keys: list[str] | None = None,
    ) -> PlateauResult:
        """파라미터 고원을 검사한다.

        Args:
            results_grid: 파라미터+지표 딕셔너리 목록.
                예: [{"rsi_threshold": 5, "ma_long": 20, "sharpe": 1.2, "mdd": 0.15}]
            metric_key: 주요 지표 키 (기본 "sharpe").
            mdd_key: MDD 키 (기본 "mdd").
            param_keys: 파라미터 키 목록 (None이면 metric_key/mdd_key 제외 자동 추론).

        Returns:
            PlateauResult.
        """
        if not results_grid:
            raise ValueError("results_grid 가 비어 있습니다.")

        if param_keys is None:
            param_keys = [k for k in results_grid[0] if k not in (metric_key, mdd_key)]

        # 최선 조합 찾기
        best = max(results_grid, key=lambda x: x.get(metric_key, float("-inf")))
        best_metric = float(best.get(metric_key, 0.0))
        best_mdd = float(best.get(mdd_key, 0.0))

        # 각 파라미터의 정렬된 고유값 → 인덱스 매핑
        param_sorted_vals: dict[str, list] = {
            k: sorted({r[k] for r in results_grid}) for k in param_keys
        }
        param_to_idx: dict[str, dict] = {
            k: {v: i for i, v in enumerate(vals)}
            for k, vals in param_sorted_vals.items()
        }

        def to_idx(combo: dict) -> tuple:
            return tuple(param_to_idx[k][combo[k]] for k in param_keys)

        best_idx = to_idx(best)

        # 이웃 찾기 (Manhattan distance == 1)
        neighbors = [
            r for r in results_grid
            if r is not best and sum(abs(a - b) for a, b in zip(best_idx, to_idx(r))) == 1
        ]

        # 이웃 통과 여부
        passing = sum(
            1 for n in neighbors
            if (
                float(n.get(metric_key, 0.0)) >= best_metric * self._metric_ratio
                and abs(float(n.get(mdd_key, 0.0))) <= abs(best_mdd) * self._mdd_ratio
            )
        )

        score = (passing / len(neighbors) * 100.0) if neighbors else 0.0
        grade = self.grade_plateau(score)

        return PlateauResult(
            best_combo={k: best[k] for k in param_keys},
            best_metric=best_metric,
            score=score,
            grade=grade,
            passed=grade in ("robust", "moderate"),
            neighbor_count=len(neighbors),
            passing_neighbors=passing,
        )

    def grade_plateau(self, score: float) -> str:
        """고원 등급을 반환한다.

        Returns:
            "robust" (>=70), "moderate" (40~70), "fragile" (<40).
        """
        if score >= self._robust:
            return "robust"
        if score >= self._moderate:
            return "moderate"
        return "fragile"
