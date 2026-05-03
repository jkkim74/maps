"""승격 게이트 — 전략 승격 결정 및 감사 로그 기록.

핵심 원칙: 알 수 없는 전략 ID나 없는 메트릭은 KeyError로 죽으면 안 된다.
  → "fail with reason"으로 처리한다.
단, MOCK_CANDIDATE 이상 단계에서 STRATEGY_GROUP_MAP에 없는 strategy_id 는
UnknownStrategyError 를 발생시킨다.
"""

from __future__ import annotations

import datetime
import json
import logging
from dataclasses import dataclass, field
from enum import Enum

from sqlalchemy.orm import Session

from maps.common.constants import (
    ALLOWED_MDD,
    STRATEGY_GROUP_MAP,
    TRADEABILITY_THRESHOLDS,
    WEIGHT_PRESETS,
)
from maps.common.exceptions import UnknownStrategyError
from maps.common.models import PromotionHistory

logger = logging.getLogger(__name__)

_DEFAULT_WEIGHTS = WEIGHT_PRESETS["balanced"]

# mc_within_limit 체크가 필요한 단계
_MC_CHECK_STAGES: frozenset[str] = frozenset(
    ["mock_candidate", "live_candidate", "live"]
)


class PromotionStage(str, Enum):
    RESEARCH = "research"
    ALERT_ONLY = "alert_only"
    MOCK_CANDIDATE = "mock_candidate"
    LIVE_CANDIDATE = "live_candidate"
    LIVE = "live"
    REJECTED = "rejected"


@dataclass
class PromotionDecision:
    """승격 결정 결과."""

    strategy_id: str
    score: float
    current_stage: PromotionStage
    target_stage: PromotionStage
    passed: bool
    reasons: list[str] = field(default_factory=list)
    evaluated_at: datetime.datetime = field(default_factory=datetime.datetime.now)


class PromotionGate:
    """전략 승격 게이트.

    모르는 메트릭은 항상 "fail with reason"으로 처리한다.
    MOCK_CANDIDATE 이상에서 알 수 없는 전략 ID → UnknownStrategyError.
    절대 KeyError 로 죽지 않는다.
    """

    def __init__(
        self,
        db: Session,
        weight_preset: dict[str, float] | None = None,
    ) -> None:
        self._db = db
        self._weights = weight_preset or _DEFAULT_WEIGHTS

    def evaluate(
        self,
        strategy_id: str,
        metrics: dict[str, float],
        current_stage: PromotionStage,
        strategy_group: str | None = None,
    ) -> PromotionDecision:
        """전략의 승격 여부를 평가한다.

        Args:
            strategy_id: 전략 ID.
            metrics: 지표 딕셔너리 (robustness, risk, recovery, return 등).
            current_stage: 현재 승격 단계.
            strategy_group: 전략군 (None이면 STRATEGY_GROUP_MAP 에서 조회).

        Returns:
            PromotionDecision. 오류 시 passed=False 로 반환 (예외 없음).

        Raises:
            UnknownStrategyError: MOCK_CANDIDATE 이상 단계에서 strategy_id 를
                STRATEGY_GROUP_MAP 에서 찾지 못한 경우.
        """
        reasons: list[str] = []

        # MOCK_CANDIDATE 이상: strategy_group 필수 → UnknownStrategyError
        resolved_group = strategy_group
        if current_stage.value in _MC_CHECK_STAGES:
            if resolved_group is None:
                resolved_group = STRATEGY_GROUP_MAP.get(strategy_id)
            if resolved_group is None:
                raise UnknownStrategyError(
                    f"알 수 없는 전략 ID: '{strategy_id}' (STRATEGY_GROUP_MAP 에 없음)"
                )

        # ── 즉시 실패 가드: 분모 0 방어 ──
        self._check_guards(metrics, reasons)

        # ── MC 한도 검증 (MOCK_CANDIDATE 이상) ──
        if current_stage.value in _MC_CHECK_STAGES and resolved_group:
            self._check_mc_limit(metrics, resolved_group, reasons)

        # ── 점수 계산 ──
        score = 0.0
        try:
            score = self._compute_score(metrics, reasons)
        except Exception as exc:
            reasons.append(f"점수 계산 오류: {exc}")
            return PromotionDecision(
                strategy_id=strategy_id,
                score=0.0,
                current_stage=current_stage,
                target_stage=PromotionStage.REJECTED,
                passed=False,
                reasons=reasons,
            )

        threshold, target_stage = self._get_threshold(current_stage)
        passed = score >= threshold and len(reasons) == 0

        if not passed and score < threshold:
            reasons.append(f"점수 {score:.1f} < 임계값 {threshold}")

        decision = PromotionDecision(
            strategy_id=strategy_id,
            score=score,
            current_stage=current_stage,
            target_stage=target_stage if passed else PromotionStage.REJECTED,
            passed=passed,
            reasons=reasons,
        )
        self._log_decision(decision)
        return decision

    # ------------------------------------------------------------------

    def _check_guards(self, metrics: dict[str, float], reasons: list[str]) -> None:
        """분모 0 방어: 특정 지표가 존재하고 <= 0 이면 즉시 실패 reason 추가."""
        is_cagr = metrics.get("is_cagr")
        if is_cagr is not None and is_cagr <= 0:
            reasons.append(f"is_cagr={is_cagr} <= 0 (즉시 실패)")

        mock_sharpe = metrics.get("mock_sharpe")
        if mock_sharpe is not None and mock_sharpe <= 0:
            reasons.append(f"mock_sharpe={mock_sharpe} <= 0 (즉시 실패)")

    def _check_mc_limit(
        self,
        metrics: dict[str, float],
        strategy_group: str,
        reasons: list[str],
    ) -> None:
        """MC MDD p95 한도 검증."""
        mc_mdd = metrics.get("mc_mdd_p95")
        if mc_mdd is None:
            reasons.append("메트릭 누락: 'mc_mdd_p95' (MC 한도 검증 불가)")
            return
        limit = ALLOWED_MDD[strategy_group]["mc_p95_limit"]
        if abs(mc_mdd) > limit:
            reasons.append(
                f"MC MDD p95={mc_mdd:.2%} > 허용 한도={limit:.2%} [{strategy_group}]"
            )

    def _compute_score(self, metrics: dict[str, float], reasons: list[str]) -> float:
        """가중치 합산 점수 (0~100). 누락 메트릭은 reasons에 기록하고 0점 처리."""
        score = 0.0
        for key, weight in self._weights.items():
            value = metrics.get(key)
            if value is None:
                reasons.append(f"메트릭 누락: '{key}'")
                continue
            score += float(value) * weight
        return score * 100

    def _get_threshold(self, stage: PromotionStage) -> tuple[int, PromotionStage]:
        mapping: dict[PromotionStage, tuple[int, PromotionStage]] = {
            PromotionStage.RESEARCH: (
                TRADEABILITY_THRESHOLDS["mock_candidate"],
                PromotionStage.MOCK_CANDIDATE,
            ),
            PromotionStage.ALERT_ONLY: (
                TRADEABILITY_THRESHOLDS["mock_candidate"],
                PromotionStage.MOCK_CANDIDATE,
            ),
            PromotionStage.MOCK_CANDIDATE: (
                TRADEABILITY_THRESHOLDS["live_candidate"],
                PromotionStage.LIVE_CANDIDATE,
            ),
            PromotionStage.LIVE_CANDIDATE: (
                TRADEABILITY_THRESHOLDS["live_candidate"],
                PromotionStage.LIVE,
            ),
        }
        return mapping.get(stage, (999, PromotionStage.REJECTED))

    def _log_decision(self, decision: PromotionDecision) -> None:
        logger.info(
            "PromotionGate [%s]: score=%.1f, passed=%s, reasons=%s",
            decision.strategy_id,
            decision.score,
            decision.passed,
            decision.reasons,
        )
        self._db.add(
            PromotionHistory(
                strategy_id=decision.strategy_id,
                from_stage=decision.current_stage.value,
                to_stage=decision.target_stage.value,
                tradeability_score=decision.score,
                passed=decision.passed,
                fail_reasons_json=(
                    json.dumps(decision.reasons, ensure_ascii=False)
                    if decision.reasons
                    else None
                ),
                evaluated_at=decision.evaluated_at,
            )
        )
        self._db.commit()
