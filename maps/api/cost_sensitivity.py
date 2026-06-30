"""SCR-12 Cost Sensitivity API."""

from __future__ import annotations

import datetime as dt
import math

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from maps.api.deps import get_db
from maps.api.schemas import CostAssumption, CostScenarioItem, CostSensitivityResponse
from maps.backtest.cost_model import BROKER_FEE_PER_SIDE, SLIPPAGE_LARGE_CAP, SLIPPAGE_SMALL_CAP, TRANSACTION_TAX_SELL
from maps.common.constants import ALLOWED_MDD, STRATEGY_GROUP_MAP, WEIGHT_PRESETS
from maps.common.models import (
    CostModelAssumptions,
    MonteCarloSequenceResults,
    ParameterPlateauResults,
    WalkForwardResults,
)

router = APIRouter(prefix="/api/v1/cost-sensitivity", tags=["SCR-12 Cost Sensitivity"])

_SCENARIOS = [
    ("Slippage -50%", -0.50),
    ("Slippage -25%", -0.25),
    ("Base (0%)", 0.00),
    ("Slippage +25%", 0.25),
    ("Slippage +50%", 0.50),
]


def _safe(v: float | None) -> float | None:
    if v is None or math.isinf(v) or math.isnan(v):
        return None
    return v


@router.get("", response_model=CostSensitivityResponse)
def get_cost_sensitivity(
    strategy_id: str = Query(default="pullback_v3"),
    db: Session = Depends(get_db),
) -> CostSensitivityResponse:
    """Return base cost assumptions and slippage sensitivity scenarios."""
    assumption = _cost_assumption(db)

    latest_wfa = (
        db.query(WalkForwardResults)
        .filter(WalkForwardResults.strategy_id == strategy_id)
        .order_by(WalkForwardResults.run_date.desc(), WalkForwardResults.id.desc())
        .first()
    )
    latest_mc = (
        db.query(MonteCarloSequenceResults)
        .filter(MonteCarloSequenceResults.strategy_id == strategy_id)
        .order_by(MonteCarloSequenceResults.run_date.desc())
        .first()
    )
    latest_plateau = (
        db.query(ParameterPlateauResults)
        .filter(ParameterPlateauResults.strategy_id == strategy_id)
        .order_by(ParameterPlateauResults.run_date.desc())
        .first()
    )

    # 기준 시나리오 Tradeability 계산 (robustness.py 동일 로직)
    base_tradeability: float | None = None
    base_sharpe: float | None = _safe(latest_wfa.sharpe_mean if latest_wfa else None)
    if latest_plateau and latest_mc and latest_wfa:
        weights = WEIGHT_PRESETS["balanced"]
        r = latest_plateau.positive_ratio * 100
        ri = (1.0 - min(abs(latest_mc.mdd_p95) / latest_mc.mdd_limit, 1.0)) * 100
        rec = min(_safe(latest_wfa.mean_g2p) / 2.0, 1.0) * 100 if _safe(latest_wfa.mean_g2p) else 0.0
        ret = min(max(_safe(latest_wfa.sharpe_mean) / 2.0, 0.0), 1.0) * 100 if _safe(latest_wfa.sharpe_mean) else 0.0
        base_tradeability = round(
            weights["robustness"] * r + weights["risk"] * ri
            + weights["recovery"] * rec + weights["return"] * ret, 1
        )

    scenarios = []
    for label, delta in _SCENARIOS:
        is_base = delta == 0
        status = "baseline" if is_base else "scenario"
        # net_cagr: 백테스트 CAGR 데이터 미보유 → None
        # net_sharpe: 기준 시나리오만 WFA 실측값, 나머지는 미계산
        # tradeability: 기준 시나리오만 계산값, 나머지는 미계산
        scenarios.append(
            CostScenarioItem(
                label=label,
                slip_delta_pct=delta,
                net_cagr=None,
                net_sharpe=base_sharpe if is_base else None,
                tradeability=base_tradeability if is_base else None,
                status=status,
            )
        )

    return CostSensitivityResponse(
        strategy_id=strategy_id,
        assumption=assumption,
        scenarios=scenarios,
        actual_large_slip=assumption.slippage_large,
        actual_mid_small_slip=assumption.slippage_mid_small,
    )


def _cost_assumption(db: Session) -> CostAssumption:
    row = db.query(CostModelAssumptions).order_by(CostModelAssumptions.effective_at.desc()).first()
    if row:
        return CostAssumption(
            tax_rate=row.tax_rate,
            commission_rate=row.commission_rate,
            slippage_large=row.slippage_rate,
            slippage_mid_small=row.slippage_rate * 3,
            effective_at=row.effective_at.isoformat() if row.effective_at else "",
        )

    return CostAssumption(
        tax_rate=TRANSACTION_TAX_SELL,
        commission_rate=BROKER_FEE_PER_SIDE,
        slippage_large=SLIPPAGE_LARGE_CAP,
        slippage_mid_small=SLIPPAGE_SMALL_CAP,
        effective_at=dt.date.today().isoformat(),
    )
