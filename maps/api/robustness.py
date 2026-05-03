"""SCR-08 Trend Robustness API — P0."""

from __future__ import annotations

import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from maps.api.deps import get_db
from maps.api.schemas import RobustnessResponse, TradeabilityBreakdown
from maps.common.models import (
    MonteCarloSequenceResults,
    ParameterPlateauResults,
    WalkForwardResults,
)
from maps.common.constants import WEIGHT_PRESETS

router = APIRouter(prefix="/api/v1/robustness", tags=["SCR-08 Robustness"])


@router.get("", response_model=RobustnessResponse)
def get_robustness(
    strategy_id: str = Query(default="pullback_v3"),
    weight_preset: str = Query(default="balanced"),
    db: Session = Depends(get_db),
) -> RobustnessResponse:
    """전략의 견고성 지표를 반환한다."""
    plateau = (
        db.query(ParameterPlateauResults)
        .filter(ParameterPlateauResults.strategy_id == strategy_id)
        .order_by(ParameterPlateauResults.run_date.desc())
        .first()
    )
    mc = (
        db.query(MonteCarloSequenceResults)
        .filter(MonteCarloSequenceResults.strategy_id == strategy_id)
        .order_by(MonteCarloSequenceResults.run_date.desc())
        .first()
    )
    wfa = (
        db.query(WalkForwardResults)
        .filter(WalkForwardResults.strategy_id == strategy_id)
        .order_by(WalkForwardResults.run_date.desc())
        .first()
    )

    weights = WEIGHT_PRESETS.get(weight_preset, WEIGHT_PRESETS["balanced"])

    breakdown: TradeabilityBreakdown | None = None
    tradeability: float | None = None

    if plateau and mc and wfa:
        robustness_score = plateau.positive_ratio * 100
        risk_score = (1.0 - min(abs(mc.mdd_p95) / mc.mdd_limit, 1.0)) * 100
        recovery_score = min(wfa.mean_g2p / 2.0, 1.0) * 100
        return_score = min(max(wfa.sharpe_mean / 2.0, 0.0), 1.0) * 100

        breakdown = TradeabilityBreakdown(
            robustness=robustness_score,
            risk=risk_score,
            recovery=recovery_score,
            ret=return_score,
            weight_preset=weight_preset,
        )
        tradeability = (
            weights["robustness"] * robustness_score
            + weights["risk"] * risk_score
            + weights["recovery"] * recovery_score
            + weights["return"] * return_score
        )

    return RobustnessResponse(
        strategy_id=strategy_id,
        tradeability_score=round(tradeability, 1) if tradeability is not None else None,
        plateau_score=plateau.positive_ratio * 100 if plateau else None,
        mc_mdd_p95=mc.mdd_p95 if mc else None,
        mc_mdd_limit=mc.mdd_limit if mc else None,
        bboot_mdd_p95=None,
        oos_is_g2p=wfa.mean_g2p if wfa else None,
        cross_market_score=None,
        breakdown=breakdown,
        plateau_grade=plateau.grade if plateau else None,
        run_date=plateau.run_date.isoformat() if plateau else None,
    )
