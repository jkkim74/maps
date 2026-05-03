"""전략 비교 대시보드 데이터 집계."""

from __future__ import annotations

from dataclasses import dataclass, field

from maps.backtest.engine import BacktestResult
from maps.promotion.gate import PromotionDecision, PromotionStage
from maps.validation.monte_carlo import MonteCarloResult
from maps.validation.walk_forward import WalkForwardResult


@dataclass
class StrategySnapshot:
    """대시보드 표시용 전략 스냅샷."""

    strategy_id: str
    strategy_group: str
    stage: PromotionStage
    promotion_score: float
    sharpe_ratio: float
    max_drawdown: float
    mc_mdd_p95: float
    wf_passed: bool
    wf_fail_reasons: list[str] = field(default_factory=list)
    promotion_fail_reasons: list[str] = field(default_factory=list)


class StrategyCompare:
    """여러 전략의 성과를 비교하고 순위를 산출한다."""

    def build_snapshot(
        self,
        strategy_id: str,
        strategy_group: str,
        backtest: BacktestResult,
        wf_result: WalkForwardResult,
        mc_result: MonteCarloResult,
        promotion: PromotionDecision,
    ) -> StrategySnapshot:
        """개별 전략의 종합 스냅샷을 생성한다.

        Args:
            strategy_id: 전략 ID.
            strategy_group: 전략군.
            backtest: 백테스트 결과.
            wf_result: Walk-Forward 결과.
            mc_result: Monte Carlo 결과.
            promotion: 승격 결정.

        Returns:
            StrategySnapshot.
        """
        return StrategySnapshot(
            strategy_id=strategy_id,
            strategy_group=strategy_group,
            stage=promotion.target_stage,
            promotion_score=promotion.score,
            sharpe_ratio=backtest.sharpe_ratio,
            max_drawdown=backtest.max_drawdown,
            mc_mdd_p95=mc_result.mdd_p95,
            wf_passed=wf_result.passed,
            wf_fail_reasons=wf_result.fail_reasons,
            promotion_fail_reasons=promotion.reasons,
        )

    def rank(self, snapshots: list[StrategySnapshot]) -> list[StrategySnapshot]:
        """승격 점수 기준 내림차순으로 정렬한다.

        Args:
            snapshots: 비교할 스냅샷 목록.

        Returns:
            정렬된 스냅샷 목록.
        """
        return sorted(snapshots, key=lambda s: s.promotion_score, reverse=True)

    def filter_by_stage(
        self,
        snapshots: list[StrategySnapshot],
        stage: PromotionStage,
    ) -> list[StrategySnapshot]:
        """특정 단계의 전략만 필터링한다."""
        return [s for s in snapshots if s.stage == stage]
