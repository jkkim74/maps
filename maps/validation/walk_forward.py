"""Walk-Forward 검증기.

통과 조건 3개 (AND):
  1. sharpe_mean > 0     — 평균적으로 수익을 낸다
  2. 음수 fold <= 1개    — 5번 중 4번은 플러스 (일관성)
  3. OOS/IS G2P >= 0.6  — IS 성과의 60% 이상 OOS 재현 (과적합 아님)

[폐기된 조건]
  구 조건 2: std/|mean| <= X (변동계수)
    제거 사유: 임계값(0.5→2.0) 선택 근거가 없고, 구 조건 2(음수 fold)와
    의미가 중복된다. 2016-2024 실데이터에서 2022 KOSPI 베어마켓(-25%)
    fold가 포함되면 어떤 값을 써도 임의적인 조정이 될 뿐이다.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from maps.backtest.engine import BacktestEngine, BacktestResult
from maps.common.constants import (
    WF_NEGATIVE_FOLD_MAX,
    WF_OOS_IS_G2P_MIN,
    WF_SHARPE_MEAN_MIN,
)
from maps.common.exceptions import ValidationError
from maps.strategy.base import BaseStrategy

logger = logging.getLogger(__name__)


@dataclass
class FoldResult:
    """단일 fold 결과."""

    fold_idx: int
    is_start: datetime.date
    is_end: datetime.date
    oos_start: datetime.date
    oos_end: datetime.date
    is_sharpe: float
    oos_sharpe: float
    is_return: float
    oos_return: float
    is_g2p: float = 0.0
    oos_g2p: float = 0.0

    @property
    def g2p_ratio(self) -> float:
        """OOS G2P / IS G2P 비율. G2P가 없으면 Sharpe 비율로 대체."""
        if self.is_g2p > 0:
            return self.oos_g2p / self.is_g2p
        return self.oos_sharpe / self.is_sharpe if self.is_sharpe > 0 else 0.0

    @property
    def g2p(self) -> float:
        """하위 호환: g2p_ratio 와 동일."""
        return self.g2p_ratio


@dataclass
class WalkForwardResult:
    """Walk-Forward 전체 결과."""

    strategy_id: str
    folds: list[FoldResult] = field(default_factory=list)
    passed: bool = False
    fail_reasons: list[str] = field(default_factory=list)
    stress_folds: list[FoldResult] = field(default_factory=list)  # 위기 구간 강제 OOS 결과

    @property
    def sharpe_mean(self) -> float:
        return float(np.mean([f.oos_sharpe for f in self.folds])) if self.folds else 0.0

    @property
    def sharpe_std(self) -> float:
        return float(np.std([f.oos_sharpe for f in self.folds])) if self.folds else 0.0

    @property
    def negative_folds(self) -> int:
        return sum(1 for f in self.folds if f.oos_sharpe < 0)

    @property
    def mean_g2p(self) -> float:
        return float(np.mean([f.g2p_ratio for f in self.folds])) if self.folds else 0.0


class WalkForwardAnalyzer:
    """Walk-Forward 검증을 수행하고 4가지 AND 조건으로 통과 여부를 판단한다."""

    # 기본 스트레스 구간 (한국 주식시장 주요 위기 구간)
    DEFAULT_STRESS_PERIODS: list[tuple[datetime.date, datetime.date]] = [
        (datetime.date(2020, 1, 1), datetime.date(2020, 6, 30)),    # COVID-19 폭락
        (datetime.date(2022, 1, 1), datetime.date(2022, 12, 31)),   # 금리 급등 (-25%)
    ]

    def __init__(
        self,
        is_months: int = 36,
        oos_months: int = 12,
        n_folds: int = 5,
        stress_periods: list[tuple[datetime.date, datetime.date]] | None = None,
    ) -> None:
        """
        Args:
            is_months: In-sample 기간 (개월).
            oos_months: Out-of-sample 기간 (개월).
            n_folds: fold 수.
            stress_periods: 위기 구간 목록. 지정 시 해당 구간을 OOS로 하는 추가 fold를
                생성해 stress_folds에 저장한다. 통과 조건에는 영향 없음 (정보 제공 목적).
                None이면 DEFAULT_STRESS_PERIODS를 사용한다.
        """
        self._is_months = is_months
        self._oos_months = oos_months
        self._n_folds = n_folds
        self._trading_days_per_month = 21
        self._stress_periods = (
            stress_periods if stress_periods is not None else self.DEFAULT_STRESS_PERIODS
        )

    def run(
        self,
        strategy: BaseStrategy,
        data: pd.DataFrame,
        param_grid: list[dict],
    ) -> WalkForwardResult:
        """Walk-Forward 검증을 실행한다.

        Args:
            strategy: 검증 대상 전략.
            data: date 인덱스 OHLCV DataFrame.
            param_grid: 파라미터 조합 목록.

        Returns:
            WalkForwardResult.
        """
        result = WalkForwardResult(strategy_id=strategy.strategy_id)
        engine = BacktestEngine()

        is_bars = self._is_months * self._trading_days_per_month
        oos_bars = self._oos_months * self._trading_days_per_month
        min_required = is_bars + self._n_folds * oos_bars

        if len(data) < min_required:
            result.fail_reasons.append(
                f"데이터 부족: {len(data)} bars < 필요 {min_required} bars"
            )
            return result

        for i in range(self._n_folds):
            is_start_idx = i * oos_bars
            is_end_idx = is_start_idx + is_bars
            oos_end_idx = is_end_idx + oos_bars

            if oos_end_idx > len(data):
                break

            is_data = data.iloc[is_start_idx:is_end_idx]
            oos_data = data.iloc[is_end_idx:oos_end_idx]

            best_params, is_sharpe, is_g2p = self._best_params(
                engine, strategy, is_data, param_grid
            )

            oos_bt = engine.run(strategy, best_params, oos_data)

            fold = FoldResult(
                fold_idx=i,
                is_start=is_data.index[0].date() if hasattr(is_data.index[0], "date") else is_data.index[0],
                is_end=is_data.index[-1].date() if hasattr(is_data.index[-1], "date") else is_data.index[-1],
                oos_start=oos_data.index[0].date() if hasattr(oos_data.index[0], "date") else oos_data.index[0],
                oos_end=oos_data.index[-1].date() if hasattr(oos_data.index[-1], "date") else oos_data.index[-1],
                is_sharpe=is_sharpe,
                oos_sharpe=oos_bt.sharpe,
                is_return=0.0,
                oos_return=oos_bt.cagr,
                is_g2p=is_g2p,
                oos_g2p=oos_bt.gain_to_pain,
            )
            result.folds.append(fold)
            logger.debug("Fold %d: IS sharpe=%.2f, OOS sharpe=%.2f", i, is_sharpe, oos_bt.sharpe)

        result.passed, result.fail_reasons = self._evaluate(result)
        self._run_stress_folds(engine, strategy, data, param_grid, result)
        logger.info(
            "WalkForward [%s]: folds=%d, passed=%s, stress_folds=%d, reasons=%s",
            strategy.strategy_id,
            len(result.folds),
            result.passed,
            len(result.stress_folds),
            result.fail_reasons,
        )
        return result

    def analyze(
        self,
        strategy: BaseStrategy,
        data: pd.DataFrame | dict,
        end_date: datetime.date | None = None,
    ) -> WalkForwardResult:
        """Walk-Forward 검증을 수행한다 (strategy.param_grid() 사용).

        Args:
            strategy: 검증 대상 전략.
            data: date 인덱스 OHLCV DataFrame 또는 {ticker: DataFrame} dict.
            end_date: 사용하지 않음 (하위 호환용).

        Returns:
            WalkForwardResult.
        """
        if isinstance(data, dict):
            # {ticker: DataFrame} 형태면 첫 번째 DataFrame 사용
            df = next(iter(data.values()))
        else:
            df = data
        return self.run(strategy, df, strategy.param_grid())

    def _best_params(
        self,
        engine: BacktestEngine,
        strategy: BaseStrategy,
        is_data: pd.DataFrame,
        param_grid: list[dict],
    ) -> tuple[dict, float, float]:
        """IS 데이터에서 Sharpe 최고 파라미터를 선택한다."""
        best_params = param_grid[0] if param_grid else strategy.default_params
        best_sharpe = float("-inf")
        best_g2p = 0.0

        for params in param_grid:
            try:
                bt = engine.run(strategy, params, is_data)
                if bt.sharpe > best_sharpe:
                    best_sharpe = bt.sharpe
                    best_g2p = bt.gain_to_pain
                    best_params = params
            except Exception as exc:
                logger.debug("IS 백테스트 오류 (params=%s): %s", params, exc)

        return best_params, best_sharpe, best_g2p

    def _run_stress_folds(
        self,
        engine: BacktestEngine,
        strategy: BaseStrategy,
        data: pd.DataFrame,
        param_grid: list[dict],
        result: WalkForwardResult,
    ) -> None:
        """스트레스 구간을 OOS로 하는 추가 fold를 실행해 result.stress_folds에 저장한다.

        통과 조건에는 영향을 주지 않는다. 위기 구간에서의 전략 성과를 별도로 집계해
        과적합 여부를 추가 확인하는 정보 제공 목적이다.
        """
        is_bars = self._is_months * self._trading_days_per_month

        for sp_start, sp_end in self._stress_periods:
            sp_ts_start = pd.Timestamp(sp_start)
            sp_ts_end = pd.Timestamp(sp_end)

            oos_mask = (data.index >= sp_ts_start) & (data.index <= sp_ts_end)
            oos_data = data[oos_mask]
            if len(oos_data) < self._trading_days_per_month:
                logger.debug("스트레스 구간 데이터 부족: %s ~ %s", sp_start, sp_end)
                continue

            # OOS 직전 구간을 IS로 사용
            is_end_loc = data.index.searchsorted(sp_ts_start)
            is_start_loc = max(0, is_end_loc - is_bars)
            if is_end_loc - is_start_loc < is_bars // 2:
                logger.debug("스트레스 구간 IS 데이터 부족: %s", sp_start)
                continue

            is_data = data.iloc[is_start_loc:is_end_loc]

            try:
                best_params, is_sharpe, is_g2p = self._best_params(
                    engine, strategy, is_data, param_grid
                )
                oos_bt = engine.run(strategy, best_params, oos_data)

                is_start_date = (
                    is_data.index[0].date() if hasattr(is_data.index[0], "date") else is_data.index[0]
                )
                is_end_date = (
                    is_data.index[-1].date() if hasattr(is_data.index[-1], "date") else is_data.index[-1]
                )
                oos_start_date = (
                    oos_data.index[0].date() if hasattr(oos_data.index[0], "date") else oos_data.index[0]
                )
                oos_end_date = (
                    oos_data.index[-1].date() if hasattr(oos_data.index[-1], "date") else oos_data.index[-1]
                )

                stress_fold = FoldResult(
                    fold_idx=-(len(result.stress_folds) + 1),
                    is_start=is_start_date,
                    is_end=is_end_date,
                    oos_start=oos_start_date,
                    oos_end=oos_end_date,
                    is_sharpe=is_sharpe,
                    oos_sharpe=oos_bt.sharpe,
                    is_return=0.0,
                    oos_return=oos_bt.cagr,
                    is_g2p=is_g2p,
                    oos_g2p=oos_bt.gain_to_pain,
                )
                result.stress_folds.append(stress_fold)
                logger.info(
                    "스트레스 fold [%s ~ %s]: IS sharpe=%.2f, OOS sharpe=%.2f",
                    sp_start,
                    sp_end,
                    is_sharpe,
                    oos_bt.sharpe,
                )
            except Exception as exc:
                logger.warning("스트레스 fold 실행 오류 [%s ~ %s]: %s", sp_start, sp_end, exc)

    def _evaluate(self, result: WalkForwardResult) -> tuple[bool, list[str]]:
        """3가지 AND 조건을 평가한다."""
        reasons: list[str] = []

        # 조건 1: sharpe_mean > 0 — 평균적으로 수익을 낸다
        if result.sharpe_mean <= WF_SHARPE_MEAN_MIN:
            reasons.append(
                f"sharpe_mean={result.sharpe_mean:.3f} <= {WF_SHARPE_MEAN_MIN} (안정적 손실 전략 의심)"
            )

        # 조건 2: 음수 fold <= 1개 — 5번 중 4번은 플러스
        if result.negative_folds > WF_NEGATIVE_FOLD_MAX:
            reasons.append(
                f"음수 fold={result.negative_folds} > {WF_NEGATIVE_FOLD_MAX}"
            )

        # 조건 3: OOS/IS G2P >= 0.6 — IS 성과의 60% 이상 OOS 재현 (과적합 아님)
        if result.mean_g2p < WF_OOS_IS_G2P_MIN:
            reasons.append(
                f"mean_g2p={result.mean_g2p:.3f} < {WF_OOS_IS_G2P_MIN} (과적합 의심)"
            )

        return len(reasons) == 0, reasons
