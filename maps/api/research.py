"""SCR-10 Research Strategies API."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from maps.api.deps import get_db
from maps.api.schemas import ResearchResponse, ResearchStrategyItem
from maps.common.models import PromotionHistory

router = APIRouter(prefix="/api/v1/research", tags=["SCR-10 Research"])


@router.get("", response_model=ResearchResponse)
def get_research(db: Session = Depends(get_db)) -> ResearchResponse:
    """Research/Alert Only 단계 전략 목록을 반환한다."""
    research_stages = {"research", "alert_only"}

    promotions = (
        db.query(PromotionHistory)
        .filter(PromotionHistory.to_stage.in_(research_stages))
        .order_by(PromotionHistory.evaluated_at.desc())
        .all()
    )

    latest: dict[str, PromotionHistory] = {}
    for p in promotions:
        if p.strategy_id not in latest:
            latest[p.strategy_id] = p

    strategies = [
        ResearchStrategyItem(
            strategy_id=sid,
            strategy_type="unknown",
            stage=p.to_stage,
            signal_count=None,
            mock_cagr=None,
            mock_mdd=None,
            observation_months=None,
            next_gate="승격 게이트 평가 필요",
        )
        for sid, p in latest.items()
    ]

    alert_only_count = sum(1 for s in strategies if s.stage == "alert_only")
    mock_count = sum(1 for s in strategies if s.stage == "research")

    return ResearchResponse(
        strategies=strategies,
        total=len(strategies),
        alert_only_count=alert_only_count,
        mock_count=mock_count,
    )
