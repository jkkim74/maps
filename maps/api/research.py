"""SCR-10 Research Strategies API."""

from __future__ import annotations

import datetime
import json
import math

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from maps.api.deps import get_db
from maps.api.schemas import ResearchResponse, ResearchStrategyItem
from maps.common.constants import STRATEGY_GROUP_MAP
from maps.common.models import (
    CandidateSnapshot,
    MonteCarloSequenceResults,
    PromotionHistory,
    WalkForwardResults,
)

router = APIRouter(prefix="/api/v1/research", tags=["SCR-10 Research"])


@router.get("", response_model=ResearchResponse)
def get_research(db: Session = Depends(get_db)) -> ResearchResponse:
    """Return strategies that are still in research or alert-only stages."""
    research_stages = {"research", "alert_only"}

    # passed=True 레코드만 읽어 현재 단계를 결정한다.
    promotions = (
        db.query(PromotionHistory)
        .filter(PromotionHistory.passed.is_(True))
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
    # 관측 기간 계산: 전략별 최초 WFA 실행일
    earliest_wfa_date: dict[str, datetime.date] = {}
    for row in db.query(WalkForwardResults).order_by(WalkForwardResults.run_date.asc()).all():
        if row.strategy_id not in earliest_wfa_date:
            earliest_wfa_date[row.strategy_id] = row.run_date

    latest_mc = _latest_by_strategy(
        db.query(MonteCarloSequenceResults)
        .order_by(MonteCarloSequenceResults.run_date.desc(), MonteCarloSequenceResults.id.desc())
        .all()
    )

    # 최신 날짜 기준 전략별 후보 종목 수
    latest_candidate_date = db.query(func.max(CandidateSnapshot.ref_date)).scalar()
    signal_counts: dict[str, int] = {}
    if latest_candidate_date:
        rows = (
            db.query(CandidateSnapshot.strategy_id, func.count(CandidateSnapshot.id))
            .filter(CandidateSnapshot.ref_date == latest_candidate_date)
            .group_by(CandidateSnapshot.strategy_id)
            .all()
        )
        signal_counts = {sid: cnt for sid, cnt in rows}

    strategy_ids = sorted(
        set(STRATEGY_GROUP_MAP)
        | set(latest_promotions)
        | set(latest_wfa)
        | set(latest_mc)
    )
    today = datetime.date.today()
    strategies = [
        _to_research_item(
            strategy_id,
            latest_promotions.get(strategy_id),
            latest_wfa.get(strategy_id),
            latest_mc.get(strategy_id),
            earliest_wfa_date.get(strategy_id),
            signal_counts.get(strategy_id, 0),
            today,
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


def _safe_float(value: float | None) -> float:
    """Infinity/NaN을 0.0으로 대체한다."""
    if value is None or math.isinf(value) or math.isnan(value):
        return 0.0
    return value


def _to_research_item(
    strategy_id: str,
    promotion: PromotionHistory | None,
    wfa: WalkForwardResults | None,
    mc: MonteCarloSequenceResults | None,
    first_wfa_date: datetime.date | None,
    signal_count: int,
    today: datetime.date,
) -> ResearchStrategyItem:
    stage = promotion.to_stage if promotion else "research"
    next_gate = "Mock Candidate gate"
    if promotion and not promotion.passed and promotion.fail_reasons_json:
        reasons = json.loads(promotion.fail_reasons_json)
        if reasons:
            next_gate = reasons[0]

    # 관측 기간: 최초 WFA 실행일 ~ 오늘 (개월 수)
    observation_months = 0.0
    if first_wfa_date:
        observation_months = round((today - first_wfa_date).days / 30.0, 1)

    # Mock Sharpe (WFA sharpe_mean, ±Inf/NaN 처리)
    mock_cagr = _safe_float(wfa.sharpe_mean if wfa else None)

    return ResearchStrategyItem(
        strategy_id=strategy_id,
        strategy_type=STRATEGY_GROUP_MAP.get(strategy_id, "unknown"),
        stage=stage,
        signal_count=signal_count,
        mock_cagr=mock_cagr,
        mock_mdd=_safe_float(mc.mdd_p95 if mc else None),
        observation_months=observation_months,
        next_gate=next_gate,
    )
