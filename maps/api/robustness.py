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
from maps.common.constants import ALLOWED_MDD, STRATEGY_GROUP_MAP, WEIGHT_PRESETS

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
            weights=weights,
        )
        tradeability = (
            weights["robustness"] * robustness_score
            + weights["risk"] * risk_score
            + weights["recovery"] * recovery_score
            + weights["return"] * return_score
        )
    else:
        breakdown = TradeabilityBreakdown(
            robustness=0.0,
            risk=0.0,
            recovery=0.0,
            ret=0.0,
            weight_preset=weight_preset,
            weights=weights,
        )

    import json as _json
    group = STRATEGY_GROUP_MAP.get(strategy_id, "portfolio_total")
    default_mdd_limit = ALLOWED_MDD.get(group, ALLOWED_MDD["portfolio_total"])["mc_p95_limit"]

    best_params: dict | None = None
    if plateau and plateau.best_params_json:
        try:
            best_params = _json.loads(plateau.best_params_json)
        except (ValueError, TypeError):
            best_params = None

    return RobustnessResponse(
        strategy_id=strategy_id,
        tradeability_score=round(tradeability, 1) if tradeability is not None else 0.0,
        plateau_score=plateau.positive_ratio * 100 if plateau else 0.0,
        mc_mdd_p95=mc.mdd_p95 if mc else 0.0,
        mc_mdd_limit=mc.mdd_limit if mc else default_mdd_limit,
        bboot_mdd_p95=0.0,
        oos_is_g2p=wfa.mean_g2p if wfa else 0.0,
        cross_market_score=0.0,
        breakdown=breakdown,
        plateau_grade=plateau.grade if plateau else "N/A",
        plateau_total=plateau.total_combinations if plateau else None,
        plateau_positive=plateau.positive_combinations if plateau else None,
        plateau_best_params=best_params,
        run_date=plateau.run_date.isoformat() if plateau else datetime.date.today().isoformat(),
    )
