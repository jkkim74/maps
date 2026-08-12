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
from maps.common.settings import get_settings

logger = logging.getLogger(__name__)

_DEFAULT_WEIGHTS = WEIGHT_PRESETS["balanced"]

# mc_within_limit 체크가 필요한 단계
_MC_CHECK_STAGES: frozenset[str] = frozenset(
    ["mock_candidate", "live_candidate", "live"]
)
_MIN_MOCK_MONTHS_FOR_LIVE_SMALL = 3
_MIN_REPLAY_TRADING_DAYS_FOR_LIVE_SMALL = 63


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
    # 이번 평가에서 자동 강등(mock_candidate → research)이 발동했는가
    demoted: bool = False


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
        if metrics.get("market_data_ready") is False:
            reasons.append("시장 점수 100% 실측 미충족")
        if metrics.get("candidate_data_ready") is False:
            reasons.append("진입 후보 점수 100% 실측 미충족")

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

        # Mock -> Live Small: real account entry requires either three months
        # of mock trading or an explicitly approved replay equivalent.
        if current_stage == PromotionStage.MOCK_CANDIDATE:
            self._check_live_small_readiness(metrics, reasons)

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

        # 승격 실패는 '탈락'이 아니라 '현재 단계 유지'다. 다음 단계 기준 미달
        # (예: mock_candidate 가 live_candidate 점수/트랙레코드 기준 미달)이어도
        # REJECTED 로 강등하지 않고 current_stage 에 머문다 — 그래야 mock_candidate
        # 전략이 주문 자격(eligible_stages)을 잃지 않는다.
        decision = PromotionDecision(
            strategy_id=strategy_id,
            score=score,
            current_stage=current_stage,
            target_stage=target_stage if passed else current_stage,
            passed=passed,
            reasons=reasons,
        )
        self._log_decision(decision)
        # 승격의 비대칭 보완: 점수가 강등 임계 미만으로 N회 연속이면 mock → research.
        # live 계열 자동 강등은 하지 않는다 (실계좌 영향 — 사람 결정).
        if not passed and current_stage == PromotionStage.MOCK_CANDIDATE:
            self._maybe_demote(decision)
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

    def _check_live_small_readiness(
        self,
        metrics: dict[str, float],
        reasons: list[str],
    ) -> None:
        """Require 3 months of Mock or an equivalent replay before Live Small."""
        mock_months = float(metrics.get("mock_months", 0.0) or 0.0)
        if mock_months >= _MIN_MOCK_MONTHS_FOR_LIVE_SMALL:
            return

        replay_passed = bool(metrics.get("replay_equivalent_passed", 0.0))
        replay_days = int(metrics.get("replay_trading_days", 0.0) or 0)
        if replay_passed and replay_days >= _MIN_REPLAY_TRADING_DAYS_FOR_LIVE_SMALL:
            return

        reasons.append(
            "Live Small 진입 차단: Mock 3개월 미충족 및 동등 리플레이 검증 미충족 "
            f"(mock_months={mock_months:.1f}, replay_days={replay_days}, replay_passed={replay_passed})"
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

    def _maybe_demote(self, decision: PromotionDecision) -> None:
        """점수가 강등 임계 미만으로 N회 연속이면 mock_candidate → research 강등.

        현재 단계는 어디서나 "최근 passed=True 행의 to_stage"로 읽히므로,
        passed=True 인 강등 행 하나로 주문 자격·화면·게이트 입력이 전부 바뀐다.
        재승격은 기존 경로 그대로 (research → mock, 점수 60 재통과 필요).
        """
        threshold = TRADEABILITY_THRESHOLDS["demotion"]
        if decision.score >= threshold:
            return
        n = get_settings().maps_demotion_consecutive_evals
        # 방금 _log_decision 으로 기록한 오늘 실패 행을 포함한 최근 n회
        recent = (
            self._db.query(PromotionHistory)
            .filter(PromotionHistory.strategy_id == decision.strategy_id)
            .order_by(PromotionHistory.evaluated_at.desc(), PromotionHistory.id.desc())
            .limit(n)
            .all()
        )
        # n회 미만이거나, 윈도 안에 승격 행(passed=True)·mock 외 단계·임계 이상
        # 점수가 하나라도 섞여 있으면 연속 미달이 아니다.
        if len(recent) < n or any(
            row.passed
            or row.from_stage != PromotionStage.MOCK_CANDIDATE.value
            or row.tradeability_score >= threshold
            for row in recent
        ):
            return

        reason = f"자동 강등: 점수 {decision.score:.1f} < {threshold} 연속 {n}회 (mock→research)"
        logger.warning("PromotionGate [%s]: %s", decision.strategy_id, reason)
        self._db.add(
            PromotionHistory(
                strategy_id=decision.strategy_id,
                from_stage=PromotionStage.MOCK_CANDIDATE.value,
                to_stage=PromotionStage.RESEARCH.value,
                tradeability_score=decision.score,
                passed=True,  # 단계 판정은 최근 passed=True 행 기준 — 강등 행도 True
                fail_reasons_json=json.dumps([reason], ensure_ascii=False),
                evaluated_at=decision.evaluated_at,
            )
        )
        self._db.commit()
        decision.demoted = True
        decision.reasons.append(reason)

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
