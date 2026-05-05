"""SCR-10 Research Strategies API."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from maps.api.deps import get_db
from maps.api.schemas import ResearchResponse, ResearchStrategyItem
from maps.common.constants import STRATEGY_GROUP_MAP
from maps.common.models import MonteCarloSequenceResults, PromotionHistory, WalkForwardResults

router = APIRouter(prefix="/api/v1/research", tags=["SCR-10 Research"])


@router.get("", response_model=ResearchResponse)
def get_research(db: Session = Depends(get_db)) -> ResearchResponse:
    """Return strategies that are still in research or alert-only stages."""
    research_stages = {"research", "alert_only"}

    promotions = (
        db.query(PromotionHistory)
        .order_by(PromotionHistory.evaluated_at.desc(), PromotionHistory.id.desc())
        .all()
    )
    latest_promotions: dict[str, PromotionHistory] = {}
    for row in promotions:
        if row.strategy_id not in latest_promotions:
            latest_promotions[row.strategy_id] = row

    latest_wfa = _latest_by_strategy(
        db.query(WalkForwardResults)
        .order_by(WalkForwardResults.run_date.desc(), WalkForwardResults.id.desc())
        .all()
    )
    latest_mc = _latest_by_strategy(
        db.query(MonteCarloSequenceResults)
        .order_by(MonteCarloSequenceResults.run_date.desc(), MonteCarloSequenceResults.id.desc())
        .all()
    )

    strategy_ids = sorted(
        set(STRATEGY_GROUP_MAP)
        | set(latest_promotions)
        | set(latest_wfa)
        | set(latest_mc)
    )
    strategies = [
        _to_research_item(
            strategy_id,
            latest_promotions.get(strategy_id),
            latest_wfa.get(strategy_id),
            latest_mc.get(strategy_id),
        )
        for strategy_id in strategy_ids
        if latest_promotions.get(strategy_id) is None
        or latest_promotions[strategy_id].to_stage in research_stages
    ]

    return ResearchResponse(
        strategies=strategies,
        total=len(strategies),
        alert_only_count=sum(1 for item in strategies if item.stage == "alert_only"),
        mock_count=sum(1 for item in strategies if item.stage == "research"),
    )


def _latest_by_strategy(rows):
    latest = {}
    for row in rows:
        if row.strategy_id not in latest:
            latest[row.strategy_id] = row
    return latest


def _to_research_item(
    strategy_id: str,
    promotion: PromotionHistory | None,
    wfa: WalkForwardResults | None,
    mc: MonteCarloSequenceResults | None,
) -> ResearchStrategyItem:
    stage = promotion.to_stage if promotion else "research"
    next_gate = "Mock Candidate gate"
    if promotion and not promotion.passed and promotion.fail_reasons_json:
        reasons = json.loads(promotion.fail_reasons_json)
        if reasons:
            next_gate = reasons[0]

    return ResearchStrategyItem(
        strategy_id=strategy_id,
        strategy_type=STRATEGY_GROUP_MAP.get(strategy_id, "unknown"),
        stage=stage,
        signal_count=0,
        mock_cagr=0.0,
        mock_mdd=mc.mdd_p95 if mc else 0.0,
        observation_months=0.0,
        next_gate=next_gate,
    )
