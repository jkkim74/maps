"""SCR-02 전략 관리 API."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from maps.api.deps import get_db
from maps.api.schemas import PromotionHistoryItem, StrategiesResponse, StrategyItem
from maps.common.models import PromotionHistory

router = APIRouter(prefix="/api/v1/strategies", tags=["SCR-02 Strategies"])


@router.get("", response_model=StrategiesResponse)
def get_strategies(db: Session = Depends(get_db)) -> StrategiesResponse:
    """전략 목록과 최신 승격 상태를 반환한다."""
    promotions = (
        db.query(PromotionHistory)
        .order_by(PromotionHistory.evaluated_at.desc())
        .all()
    )

    latest: dict[str, PromotionHistory] = {}
    for p in promotions:
        if p.strategy_id not in latest:
            latest[p.strategy_id] = p

    strategies = [
        StrategyItem(
            strategy_id=sid,
            name=sid,
            stage=p.to_stage,
            tradeability_score=p.tradeability_score,
            plateau_score=None,
            mc_mdd_p95=None,
            wfa_passed=None,
            wfa_cv=None,
            promotion_pending=False,
            fail_reasons=json.loads(p.fail_reasons_json) if p.fail_reasons_json else [],
        )
        for sid, p in latest.items()
    ]

    pending = sum(1 for s in strategies if s.promotion_pending)
    return StrategiesResponse(
        strategies=strategies,
        pending_promotions=pending,
        total=len(strategies),
    )


@router.get("/history/{strategy_id}", response_model=list[PromotionHistoryItem])
def get_strategy_history(strategy_id: str, db: Session = Depends(get_db)) -> list[PromotionHistoryItem]:
    """특정 전략의 승격 이력을 반환한다."""
    rows = (
        db.query(PromotionHistory)
        .filter(PromotionHistory.strategy_id == strategy_id)
        .order_by(PromotionHistory.evaluated_at.desc())
        .limit(50)
        .all()
    )
    if not rows:
        raise HTTPException(status_code=404, detail=f"전략 '{strategy_id}' 이력 없음")

    return [
        PromotionHistoryItem(
            id=r.id,
            strategy_id=r.strategy_id,
            from_stage=r.from_stage,
            to_stage=r.to_stage,
            tradeability_score=r.tradeability_score,
            passed=r.passed,
            fail_reasons=json.loads(r.fail_reasons_json) if r.fail_reasons_json else [],
            evaluated_at=r.evaluated_at.isoformat() if r.evaluated_at else "",
        )
        for r in rows
    ]
