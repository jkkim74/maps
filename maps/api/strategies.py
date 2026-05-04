"""SCR-02 전략 관리 API."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from maps.api.deps import get_db
from maps.api.schemas import PromotionHistoryItem, StrategiesResponse, StrategyItem
from maps.common.constants import STRATEGY_GROUP_MAP, WEIGHT_PRESETS
from maps.common.models import (
    MonteCarloSequenceResults,
    ParameterPlateauResults,
    PromotionHistory,
    WalkForwardResults,
)

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

    latest_plateau = _latest_by_strategy(
        db.query(ParameterPlateauResults)
        .order_by(ParameterPlateauResults.run_date.desc(), ParameterPlateauResults.id.desc())
        .all()
    )
    latest_mc = _latest_by_strategy(
        db.query(MonteCarloSequenceResults)
        .order_by(MonteCarloSequenceResults.run_date.desc(), MonteCarloSequenceResults.id.desc())
        .all()
    )
    latest_wfa = _latest_by_strategy(
        db.query(WalkForwardResults)
        .order_by(WalkForwardResults.run_date.desc(), WalkForwardResults.id.desc())
        .all()
    )

    strategy_ids = sorted(
        set(STRATEGY_GROUP_MAP)
        | set(latest)
        | set(latest_plateau)
        | set(latest_mc)
        | set(latest_wfa)
    )

    strategies = [
        _to_strategy_item(
            sid,
            latest.get(sid),
            latest_plateau.get(sid),
            latest_mc.get(sid),
            latest_wfa.get(sid),
        )
        for sid in strategy_ids
    ]

    pending = sum(1 for s in strategies if s.promotion_pending)
    return StrategiesResponse(
        strategies=strategies,
        pending_promotions=pending,
        total=len(strategies),
    )


def _latest_by_strategy(rows):
    latest = {}
    for row in rows:
        if row.strategy_id not in latest:
            latest[row.strategy_id] = row
    return latest


def _to_strategy_item(
    strategy_id: str,
    promotion: PromotionHistory | None,
    plateau: ParameterPlateauResults | None,
    mc: MonteCarloSequenceResults | None,
    wfa: WalkForwardResults | None,
) -> StrategyItem:
    tradeability = promotion.tradeability_score if promotion else _tradeability_score(plateau, mc, wfa)
    fail_reasons = json.loads(promotion.fail_reasons_json) if promotion and promotion.fail_reasons_json else []
    return StrategyItem(
        strategy_id=strategy_id,
        name=strategy_id,
        stage=promotion.to_stage if promotion else "research",
        tradeability_score=round(tradeability, 1) if tradeability is not None else None,
        plateau_score=round(plateau.positive_ratio * 100, 1) if plateau else None,
        mc_mdd_p95=mc.mdd_p95 if mc else None,
        wfa_passed=wfa.passed if wfa else None,
        wfa_cv=_wfa_cv(wfa),
        promotion_pending=False,
        fail_reasons=fail_reasons,
    )


def _tradeability_score(
    plateau: ParameterPlateauResults | None,
    mc: MonteCarloSequenceResults | None,
    wfa: WalkForwardResults | None,
) -> float | None:
    if not (plateau and mc and wfa):
        return None
    weights = WEIGHT_PRESETS["balanced"]
    robustness_score = plateau.positive_ratio * 100
    risk_score = (1.0 - min(abs(mc.mdd_p95) / mc.mdd_limit, 1.0)) * 100 if mc.mdd_limit else 0.0
    recovery_score = min(wfa.mean_g2p / 2.0, 1.0) * 100
    return_score = min(max(wfa.sharpe_mean / 2.0, 0.0), 1.0) * 100
    return (
        weights["robustness"] * robustness_score
        + weights["risk"] * risk_score
        + weights["recovery"] * recovery_score
        + weights["return"] * return_score
    )


def _wfa_cv(wfa: WalkForwardResults | None) -> float | None:
    if not wfa or wfa.sharpe_mean == 0:
        return None
    return abs(wfa.sharpe_std / wfa.sharpe_mean)


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
