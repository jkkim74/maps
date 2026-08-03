"""Walk-Forward 검증기.

통과 조건 4개 (AND):
  1. sharpe_mean > 0        — 평균적으로 수익을 낸다
  2. 음수 fold <= 1개       — 5번 중 4번은 플러스 (일관성)
  3. OOS/IS G2P >= 0.6      — IS 성과의 60% 이상 OOS 재현 (과적합 아님)
  4. OOS 무거래 fold <= 1개 — 거래를 안 한 구간은 재현 성공이 아니다

[조건 4 추가 배경 — 2026-08-03]
  거래가 0건이면 일별 수익률이 전부 0이라 손실합도 0이 되고, 예전 engine 은 이를
  G2P=inf 로 냈다. `inf < 0.6` 이 False 라 조건 3이 **조용히 통과**했다. 실측에서
  contrarian_quality 는 5개 fold 전부 OOS 무거래인데 G2P 조건을 통과하고 있었다.
  근본 수정은 engine._gain_to_pain(무거래=0.0, 무손실=상한)이고, 조건 4는 "왜 낮은지"를
  사유로 드러내는 역할이다.

[폐기된 조건]
  구 조건 2: std/|mean| <= X (변동계수)
    제거 사유: 임계값(0.5→2.0) 선택 근거가 없고, 구 조건 2(음수 fold)와
    의미가 중복된다. 2016-2024 실데이터에서 2022 KOSPI 베어마켓(-25%)
    fold가 포함되면 어떤 값을 써도 임의적인 조정이 될 뿐이다.
"""

from __future__ import annotations

import datetime
import logging
import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from maps.backtest.engine import BacktestEngine, BacktestResult
from maps.common.constants import (
    GAIN_TO_PAIN_CAP,
    WF_NEGATIVE_FOLD_MAX,
    WF_NO_TRADE_FOLD_MAX,
    WF_OOS_IS_G2P_MIN,
    WF_SHARPE_MEAN_MIN,
)
from maps.common.exceptions import ValidationError
from maps.strategy.base import BaseStrategy

logger = logging.getLogger(__name__)

# H-1: IS 파라미터 선택 견고성 점수 가중치. Sharpe 단일 피크 과최적화를 완화하기 위해
# gain-to-pain(일관성)을 보조 가중한다.
# 캡은 `engine._gain_to_pain` 과 같은 값을 써야 한다 — 소스에서 이미 상한을 적용하므로
# 여기 캡은 (구 데이터·외부 입력 대비) 방어층으로 남는다.
_GAIN_TO_PAIN_SELECT_WEIGHT = 0.5
_GAIN_TO_PAIN_CAP = GAIN_TO_PAIN_CAP


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
    # OOS 구간 거래 수. 0이면 "재현 성공"이 아니라 **검증 표본 없음**이다.
    # G2P 값만으로는 무거래와 무손실을 구분할 수 없어 별도로 센다.
    #
    # None = 측정 안 됨(구 데이터·합성 객체). 기본값을 0으로 두면 값을 안 채운 경로가
    # 전부 "무거래"로 오인돼 거짓 실패를 낸다 — 미지와 실측 0은 반드시 구분한다.
    oos_trades: int | None = None

    @property
    def g2p_ratio(self) -> float:
        """OOS G2P / IS G2P 비율. G2P가 없으면 Sharpe 비율로 대체.

        **항상 유한값을 반환한다.** 구 데이터나 외부 입력에 inf가 섞이면
        `inf/inf = NaN`이 되고, NaN은 어떤 비교에도 False라 게이트를 조용히 통과한다.
        """
        if self.is_g2p > 0:
            ratio = self.oos_g2p / self.is_g2p
        else:
            ratio = self.oos_sharpe / self.is_sharpe if self.is_sharpe > 0 else 0.0
        return ratio if math.isfinite(ratio) else 0.0

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

    @property
    def no_trade_folds(self) -> int:
        """OOS에서 한 번도 거래하지 않은 fold 수 (측정값이 있는 fold만 센다)."""
        return sum(1 for f in self.folds if f.oos_trades == 0)  # None 은 세지 않는다


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
                oos_trades=oos_bt.total_trades,
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
        """IS 데이터에서 가장 견고한 파라미터를 선택한다.

        IS Sharpe 단일 최대 선택은 과최적화(fragile peak)에 취약하므로, 위험조정수익
        (Sharpe)에 일관성 지표(gain-to-pain)를 가중 합산한 견고성 점수로 선택한다(H-1).
        반환하는 sharpe/g2p는 선택된 파라미터의 실측값이다(폴드 메트릭 일관성 유지).
        """
        best_params = param_grid[0] if param_grid else strategy.default_params
        best_score = float("-inf")
        best_sharpe = float("-inf")
        best_g2p = 0.0

        for params in param_grid:
            try:
                bt = engine.run(strategy, params, is_data)
            except Exception as exc:
                logger.debug("IS 백테스트 오류 (params=%s): %s", params, exc)
                continue
            score = self._selection_score(bt.sharpe, bt.gain_to_pain)
            if score > best_score:
                best_score = score
                best_sharpe = bt.sharpe
                best_g2p = bt.gain_to_pain
                best_params = params

        return best_params, best_sharpe, best_g2p

    @staticmethod
    def _selection_score(sharpe: float, gain_to_pain: float) -> float:
        """파라미터 선택용 견고성 점수.

        Sharpe(위험조정수익)에 gain-to-pain(이익/손실 비율, 일관성)을 가중 합산한다.
        g2p는 무손실 시 inf가 될 수 있어 상한으로 캡한다. Sharpe가 비슷한 후보 중
        더 일관적인(손실 대비 이익이 큰) 파라미터를 선호해 IS 과최적화를 완화한다.
        """
        g2p_capped = (
            min(gain_to_pain, _GAIN_TO_PAIN_CAP)
            if math.isfinite(gain_to_pain)
            else _GAIN_TO_PAIN_CAP
        )
        return sharpe + _GAIN_TO_PAIN_SELECT_WEIGHT * g2p_capped

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
                    oos_trades=oos_bt.total_trades,
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
        # 비유한값은 통과가 아니라 실패다 — `inf < 0.6` 도 `NaN < 0.6` 도 False라
        # 그냥 두면 조건이 조용히 통과한다 (2026-08-03 발견).
        if not math.isfinite(result.mean_g2p) or result.mean_g2p < WF_OOS_IS_G2P_MIN:
            reasons.append(
                f"mean_g2p={result.mean_g2p:.3f} < {WF_OOS_IS_G2P_MIN} (과적합 의심)"
            )

        # 조건 4: OOS 무거래 fold <= 1개 — 거래를 안 한 구간은 재현 성공이 아니다.
        # G2P 수치만 보면 "왜 낮은지"를 모른다. 진짜 이유를 사유로 남긴다.
        if result.no_trade_folds > WF_NO_TRADE_FOLD_MAX:
            reasons.append(
                f"OOS 무거래 fold={result.no_trade_folds}/{len(result.folds)} "
                f"> {WF_NO_TRADE_FOLD_MAX} (검증 표본 없음)"
            )

        return len(reasons) == 0, reasons
