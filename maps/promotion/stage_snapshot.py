"""strategy-selector에 전달할 전략 승격 단계 스냅샷."""

from __future__ import annotations

from sqlalchemy.orm import Session

from maps.common.models import PromotionHistory


ELIGIBLE_SELECTOR_STAGES = frozenset({"mock_candidate", "live_candidate", "live"})


def build_strategy_stage_context(db: Session) -> dict[str, object]:
    """최신 성공 승격 이력으로 selector 입력 JSON을 만든다.

    승격 실패(`passed=False`)는 기존에 획득한 단계를 박탈하지 않는다. 자동 강등은
    `passed=True, to_stage=research`로 기록되므로 동일한 최신행 규칙에 포함된다.
    """
    rows = (
        db.query(PromotionHistory)
        .filter(PromotionHistory.passed.is_(True))
        .order_by(PromotionHistory.evaluated_at.desc(), PromotionHistory.id.desc())
        .all()
    )
    stages: dict[str, dict[str, object]] = {}
    for row in rows:
        if row.strategy_id in stages:
            continue
        stages[row.strategy_id] = {
            "stage": row.to_stage,
            "eligible": row.to_stage in ELIGIBLE_SELECTOR_STAGES,
            "evaluated_at": row.evaluated_at.isoformat(),
            "promotion_history_id": row.id,
        }

    return {
        "schema_version": 1,
        "source": "promotion_history.latest_passed",
        "eligible_stages": sorted(ELIGIBLE_SELECTOR_STAGES),
        "strategy_stages": stages,
    }
