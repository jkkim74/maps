"""Walk-Forward 검증기.

통과 조건 4개 (AND):
  1. sharpe_mean > 0
  2. std/|mean| <= 0.5
  3. 음수 fold <= 1개
  4. OOS/IS G2P >= 0.6
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
    WF_STD_MEAN_RATIO_MAX,
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

    def __init__(
        self,
        is_months: int = 36,
        oos_months: int = 12,
        n_folds: int = 5,
    ) -> None:
        """
        Args:
            is_months: In-sample 기간 (개월).
            oos_months: Out-of-sample 기간 (개월).
            n_folds: fold 수.
        """
        self._is_months = is_months
        self._oos_months = oos_months
        self._n_folds = n_folds
        self._trading_days_per_month = 21

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
        logger.info(
            "WalkForward [%s]: folds=%d, passed=%s, reasons=%s",
            strategy.strategy_id,
            len(result.folds),
            result.passed,
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

    def _evaluate(self, result: WalkForwardResult) -> tuple[bool, list[str]]:
        """4가지 AND 조건을 평가한다."""
        reasons: list[str] = []

        # 조건 1: sharpe_mean > 0
        if result.sharpe_mean <= WF_SHARPE_MEAN_MIN:
            reasons.append(
                f"sharpe_mean={result.sharpe_mean:.3f} <= {WF_SHARPE_MEAN_MIN} (안정적 손실 전략 의심)"
            )

        # 조건 2: std/|mean| <= 0.5
        mean_abs = abs(result.sharpe_mean)
        ratio = result.sharpe_std / mean_abs if mean_abs > 0 else float("inf")
        if ratio > WF_STD_MEAN_RATIO_MAX:
            reasons.append(
                f"std/|mean|={ratio:.3f} > {WF_STD_MEAN_RATIO_MAX} (불안정한 성과)"
            )

        # 조건 3: 음수 fold <= 1개
        if result.negative_folds > WF_NEGATIVE_FOLD_MAX:
            reasons.append(
                f"음수 fold={result.negative_folds} > {WF_NEGATIVE_FOLD_MAX}"
            )

        # 조건 4: OOS/IS G2P >= 0.6
        if result.mean_g2p < WF_OOS_IS_G2P_MIN:
            reasons.append(
                f"mean_g2p={result.mean_g2p:.3f} < {WF_OOS_IS_G2P_MIN} (과적합 의심)"
            )

        return len(reasons) == 0, reasons
