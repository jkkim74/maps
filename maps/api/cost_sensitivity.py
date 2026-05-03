"""SCR-12 Cost Sensitivity API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from maps.api.deps import get_db
from maps.api.schemas import (
    CostAssumption,
    CostScenarioItem,
    CostSensitivityResponse,
)
from maps.common.models import CostModelAssumptions

router = APIRouter(prefix="/api/v1/cost-sensitivity", tags=["SCR-12 Cost Sensitivity"])

_SCENARIOS = [
    ("슬립 -50%", -0.50),
    ("슬립 -25%", -0.25),
    ("가정 (0%)", 0.00),
    ("슬립 +25%", 0.25),
    ("슬립 +50%", 0.50),
]


@router.get("", response_model=CostSensitivityResponse)
def get_cost_sensitivity(
    strategy_id: str = Query(default="pullback_v3"),
    db: Session = Depends(get_db),
) -> CostSensitivityResponse:
    """비용 민감도 시나리오를 반환한다."""
    row = (
        db.query(CostModelAssumptions)
        .order_by(CostModelAssumptions.effective_at.desc())
        .first()
    )

    assumption: CostAssumption | None = None
    if row:
        assumption = CostAssumption(
            tax_rate=row.tax_rate,
            commission_rate=row.commission_rate,
            slippage_large=row.slippage_rate,
            slippage_mid_small=row.slippage_rate * 3,
            effective_at=row.effective_at.isoformat() if row.effective_at else "",
        )

    scenarios = [
        CostScenarioItem(
            label=label,
            slip_delta_pct=delta,
            net_cagr=None,
            net_sharpe=None,
            tradeability=None,
            status="데이터 없음",
        )
        for label, delta in _SCENARIOS
    ]

    return CostSensitivityResponse(
        strategy_id=strategy_id,
        assumption=assumption,
        scenarios=scenarios,
        actual_large_slip=None,
        actual_mid_small_slip=None,
    )
