"""strategy-selector 승격 단계 입력 스냅샷 테스트."""

from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from maps.common.models import PromotionHistory
from maps.promotion.stage_snapshot import build_strategy_stage_context


def _promotion(
    strategy_id: str,
    *,
    to_stage: str,
    passed: bool,
    evaluated_at: dt.datetime,
) -> PromotionHistory:
    """테스트용 승격 이력을 만든다."""
    return PromotionHistory(
        strategy_id=strategy_id,
        from_stage="research",
        to_stage=to_stage,
        tradeability_score=70.0,
        passed=passed,
        evaluated_at=evaluated_at,
    )


def test_stage_context_uses_latest_passed_row_and_ignores_newer_failure(db: Session) -> None:
    """승격 실패는 이미 획득한 단계를 박탈하지 않는다."""
    earlier = dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc)
    later = dt.datetime(2026, 8, 2, tzinfo=dt.timezone.utc)
    db.add(_promotion("ath_breakout_v1", to_stage="mock_candidate", passed=True, evaluated_at=earlier))
    db.add(_promotion("ath_breakout_v1", to_stage="rejected", passed=False, evaluated_at=later))
    db.commit()

    context = build_strategy_stage_context(db)

    stage = context["strategy_stages"]["ath_breakout_v1"]
    assert stage["stage"] == "mock_candidate"
    assert stage["eligible"] is True


def test_stage_context_respects_latest_successful_demotion(db: Session) -> None:
    """passed=True research 행은 자동 강등으로 보고 selector에서 제외한다."""
    earlier = dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc)
    later = dt.datetime(2026, 8, 2, tzinfo=dt.timezone.utc)
    db.add(_promotion("pullback_v3", to_stage="mock_candidate", passed=True, evaluated_at=earlier))
    db.add(_promotion("pullback_v3", to_stage="research", passed=True, evaluated_at=later))
    db.commit()

    context = build_strategy_stage_context(db)

    stage = context["strategy_stages"]["pullback_v3"]
    assert stage["stage"] == "research"
    assert stage["eligible"] is False
    assert context["source"] == "promotion_history.latest_passed"
