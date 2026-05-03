"""Monte Carlo 시뮬레이션 — MDD p95 검증 및 거래 순서 셔플 / 블록 부트스트랩."""

from __future__ import annotations

import logging
import warnings
from dataclasses import dataclass

import numpy as np

from maps.common.constants import ALLOWED_MDD
from maps.common.exceptions import ValidationError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 공통 결과 자료형
# ---------------------------------------------------------------------------

@dataclass
class MonteCarloResult:
    """Monte Carlo 결과 (bootstrap on daily returns)."""

    strategy_id: str
    strategy_group: str
    n_simulations: int
    mdd_p95: float
    mdd_limit: float
    passed: bool
    fail_reason: str = ""


@dataclass
class SequenceTestResult:
    """거래 순서 셔플 / 블록 부트스트랩 MDD 분포."""

    n_simulations: int
    mdd_p50: float
    mdd_p75: float
    mdd_p90: float
    mdd_p95: float


# ---------------------------------------------------------------------------
# 기존: 일별 수익률 부트스트랩 검증기
# ---------------------------------------------------------------------------

class MonteCarloValidator:
    """Monte Carlo 시뮬레이션으로 MDD p95가 전략군별 허용 한도 이내인지 검증한다."""

    def __init__(self, n_simulations: int = 10_000, seed: int | None = 42) -> None:
        self._n = n_simulations
        self._rng = np.random.default_rng(seed)

    def validate(
        self,
        strategy_id: str,
        strategy_group: str,
        daily_returns: list[float],
    ) -> MonteCarloResult:
        """Monte Carlo 시뮬레이션을 실행하고 MDD p95를 검증한다.

        Args:
            strategy_id: 전략 ID.
            strategy_group: 전략군 (ALLOWED_MDD 키).
            daily_returns: 역사적 일별 수익률 목록.

        Returns:
            MonteCarloResult.

        Raises:
            ValidationError: 전략군이 허용 목록에 없거나 수익률이 부족할 때.
        """
        if strategy_group not in ALLOWED_MDD:
            raise ValidationError(f"알 수 없는 전략군: {strategy_group}")

        limit = ALLOWED_MDD[strategy_group]["mc_p95_limit"]
        arr = np.array(daily_returns, dtype=float)

        if len(arr) < 30:
            raise ValidationError("Monte Carlo에는 최소 30개 이상의 일별 수익률이 필요합니다.")

        mdd_p95 = self._simulate(arr)
        passed = mdd_p95 <= limit
        fail_reason = (
            f"MDD p95={mdd_p95:.2%} > 허용 한도={limit:.2%}" if not passed else ""
        )

        logger.info(
            "MC [%s/%s]: mdd_p95=%.2f%%, limit=%.2f%%, passed=%s",
            strategy_id,
            strategy_group,
            mdd_p95 * 100,
            limit * 100,
            passed,
        )
        return MonteCarloResult(
            strategy_id=strategy_id,
            strategy_group=strategy_group,
            n_simulations=self._n,
            mdd_p95=mdd_p95,
            mdd_limit=limit,
            passed=passed,
            fail_reason=fail_reason,
        )

    def _simulate(self, returns: np.ndarray) -> float:
        """n회 부트스트랩 시뮬레이션 후 MDD p95를 반환한다."""
        n_days = len(returns)
        mdds: list[float] = []

        for _ in range(self._n):
            sampled = self._rng.choice(returns, size=n_days, replace=True)
            equity = np.cumprod(1 + sampled)
            peak = np.maximum.accumulate(equity)
            dd = (equity - peak) / peak
            mdds.append(float(dd.min()))

        # mdds는 음수값 목록. p5(최악 5%)를 절댓값으로 반환해야 limit 비교가 올바름
        return abs(float(np.percentile(mdds, 5)))


# ---------------------------------------------------------------------------
# 거래 순서 셔플 Monte Carlo
# ---------------------------------------------------------------------------

class MonteCarloSequenceTester:
    """거래 순서를 랜덤 셔플하여 MDD 분포를 계산한다.

    실제 거래 PnL 목록의 순서를 n회 셔플해 equity curve를 재구성한다.
    """

    def __init__(self, n_simulations: int = 1000, seed: int | None = 42) -> None:
        self._n = n_simulations
        self._rng = np.random.default_rng(seed)

    def run(
        self,
        trade_pnls: list[float],
        initial_equity: float = 1.0,
    ) -> SequenceTestResult:
        """거래 PnL 순서를 셔플하여 MDD 분포를 반환한다.

        Args:
            trade_pnls: 거래별 손익 목록 (절대 금액).
            initial_equity: 초기 자산 (기본 1.0, 정규화된 단위 사용 권장).

        Returns:
            SequenceTestResult (p50, p75, p90, p95).
        """
        arr = np.array(trade_pnls, dtype=float)
        mdds: list[float] = []

        for _ in range(self._n):
            shuffled = self._rng.permutation(arr)
            equity = np.concatenate([[initial_equity], initial_equity + np.cumsum(shuffled)])
            peak = np.maximum.accumulate(equity)
            dd = (equity - peak) / np.where(peak > 0, peak, 1.0)
            mdds.append(float(dd.min()))

        return _to_sequence_result(self._n, mdds)


# ---------------------------------------------------------------------------
# 블록 부트스트랩
# ---------------------------------------------------------------------------

class BlockBootstrapTester:
    """블록 부트스트랩으로 시계열 자기상관을 보존하면서 MDD 분포를 계산한다.

    block_size=[5,10,20] 별로 시뮬레이션하고, MonteCarloSequenceTester 대비
    p95 비율이 1.5 이상이면 군집성 위험을 경고한다.
    """

    CLUSTER_RISK_THRESHOLD: float = 1.5

    def __init__(
        self,
        block_sizes: list[int] | None = None,
        n_simulations: int = 1000,
        seed: int | None = 42,
    ) -> None:
        self._block_sizes = block_sizes or [5, 10, 20]
        self._n = n_simulations
        self._rng = np.random.default_rng(seed)

    def run(self, daily_returns: list[float]) -> dict[int, SequenceTestResult]:
        """각 block_size별 MDD 분포를 계산한다.

        Args:
            daily_returns: 일별 수익률 목록.

        Returns:
            {block_size: SequenceTestResult}.
        """
        arr = np.array(daily_returns, dtype=float)
        return {bs: self._bootstrap(arr, bs) for bs in self._block_sizes}

    def cluster_risk_ratio(
        self,
        mc_result: SequenceTestResult,
        bb_result: SequenceTestResult,
    ) -> tuple[float, bool]:
        """BBoot p95 / MC p95 비율과 군집성 위험 여부를 반환한다.

        Returns:
            (ratio, is_cluster_risk). ratio >= 1.5 이면 군집성 위험.
        """
        mc_p95 = mc_result.mdd_p95   # 이미 양수 절댓값 심각도
        bb_p95 = bb_result.mdd_p95

        if mc_p95 == 0:
            return float("inf"), True

        ratio = bb_p95 / mc_p95
        is_risk = ratio >= self.CLUSTER_RISK_THRESHOLD

        if is_risk:
            warnings.warn(
                f"군집성 위험: BBoot/MC p95 비율={ratio:.2f} >= {self.CLUSTER_RISK_THRESHOLD}",
                stacklevel=2,
            )
        return ratio, is_risk

    def _bootstrap(self, returns: np.ndarray, block_size: int) -> SequenceTestResult:
        n = len(returns)
        mdds: list[float] = []
        max_start = max(1, n - block_size + 1)

        for _ in range(self._n):
            n_blocks = (n + block_size - 1) // block_size
            starts = self._rng.integers(0, max_start, size=n_blocks)
            blocks = [returns[s : s + block_size] for s in starts]
            sampled = np.concatenate(blocks)[:n]
            equity = np.cumprod(1 + sampled)
            peak = np.maximum.accumulate(equity)
            dd = (equity - peak) / np.where(peak > 0, peak, 1.0)
            mdds.append(float(dd.min()))

        return _to_sequence_result(self._n, mdds)


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------

def _to_sequence_result(n: int, mdds: list[float]) -> SequenceTestResult:
    # mdds는 음수값. np.abs()로 손실 심각도로 변환해 표준 percentile 적용:
    # p95 = 시뮬레이션의 95%가 이 값 이하 손실 = 최악 5% 시나리오
    abs_arr = np.abs(np.array(mdds))
    return SequenceTestResult(
        n_simulations=n,
        mdd_p50=float(np.percentile(abs_arr, 50)),
        mdd_p75=float(np.percentile(abs_arr, 75)),
        mdd_p90=float(np.percentile(abs_arr, 90)),
        mdd_p95=float(np.percentile(abs_arr, 95)),
    )
