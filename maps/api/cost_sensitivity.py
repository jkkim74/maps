"""SCR-12 Cost Sensitivity API."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from maps.api.deps import get_db
from maps.api.schemas import CostAssumption, CostScenarioItem, CostSensitivityResponse
from maps.backtest.cost_model import BROKER_FEE_ROUNDTRIP, SLIPPAGE_LARGE_CAP, SLIPPAGE_SMALL_CAP, TRANSACTION_TAX_SELL
from maps.common.models import CostModelAssumptions, WalkForwardResults

router = APIRouter(prefix="/api/v1/cost-sensitivity", tags=["SCR-12 Cost Sensitivity"])

_SCENARIOS = [
    ("Slippage -50%", -0.50),
    ("Slippage -25%", -0.25),
    ("Base (0%)", 0.00),
    ("Slippage +25%", 0.25),
    ("Slippage +50%", 0.50),
]


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

    scenarios = []
    for label, delta in _SCENARIOS:
        multiplier = 1.0 + delta
        status = "baseline" if delta == 0 else "scenario"
        scenarios.append(
            CostScenarioItem(
                label=label,
                slip_delta_pct=delta,
                net_cagr=0.0,
                net_sharpe=latest_wfa.sharpe_mean if latest_wfa and delta == 0 else 0.0,
                tradeability=0.0,
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
        commission_rate=BROKER_FEE_ROUNDTRIP,
        slippage_large=SLIPPAGE_LARGE_CAP,
        slippage_mid_small=SLIPPAGE_SMALL_CAP,
        effective_at=dt.date.today().isoformat(),
    )
