"""SCR-11 Walk-Forward Report API — P0."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from maps.api.deps import get_db
from maps.api.schemas import FoldResultItem, WfaResponse
from maps.common.models import WalkForwardResults

router = APIRouter(prefix="/api/v1/wfa", tags=["SCR-11 WFA"])


@router.get("", response_model=WfaResponse)
def get_wfa(
    strategy_id: str = Query(default="pullback_v3"),
    db: Session = Depends(get_db),
) -> WfaResponse:
    """Walk-Forward 결과를 반환한다."""
    row = (
        db.query(WalkForwardResults)
        .filter(WalkForwardResults.strategy_id == strategy_id)
        .order_by(WalkForwardResults.run_date.desc())
        .first()
    )

    if not row:
        return WfaResponse(
            strategy_id=strategy_id,
            passed=False,
            sharpe_mean=None,
            cv=None,
            negative_folds=None,
            mean_g2p=None,
            fail_reasons=["결과 없음"],
            folds=[],
            run_date=None,
        )

    cv = (row.sharpe_std / abs(row.sharpe_mean)) if row.sharpe_mean != 0 else None
    fail_reasons = json.loads(row.fail_reasons_json) if row.fail_reasons_json else []

    return WfaResponse(
        strategy_id=strategy_id,
        passed=row.passed,
        sharpe_mean=row.sharpe_mean,
        cv=round(cv, 3) if cv is not None else None,
        negative_folds=row.negative_folds,
        mean_g2p=row.mean_g2p,
        fail_reasons=fail_reasons,
        folds=[],
        run_date=row.run_date.isoformat() if row.run_date else None,
    )
