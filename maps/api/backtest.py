"""SCR-07 백테스트 콘솔 API."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from maps.api.deps import get_db
from maps.api.schemas import BacktestResponse, BacktestRunItem
from maps.common.models import MonteCarloSequenceResults, WalkForwardResults

router = APIRouter(prefix="/api/v1/backtest", tags=["SCR-07 Backtest"])


@router.get("", response_model=BacktestResponse)
def get_backtest_runs(db: Session = Depends(get_db)) -> BacktestResponse:
    """최근 백테스트 실행 목록을 반환한다 (WFA 결과 기준)."""
    wfa_rows = (
        db.query(WalkForwardResults)
        .order_by(WalkForwardResults.run_date.desc(), WalkForwardResults.id.desc())
        .limit(50)
        .all()
    )

    # 전략별 최신 MC 결과 (MDD p95 표시용)
    mc_rows = (
        db.query(MonteCarloSequenceResults)
        .order_by(MonteCarloSequenceResults.run_date.desc(), MonteCarloSequenceResults.id.desc())
        .limit(200)
        .all()
    )
    latest_mc: dict[str, MonteCarloSequenceResults] = {}
    for row in mc_rows:
        if row.strategy_id not in latest_mc:
            latest_mc[row.strategy_id] = row

    runs = [
        BacktestRunItem(
            run_id=str(row.id),
            strategy_id=row.strategy_id,
            status="done",
            progress_pct=100.0,
            net_cagr=None,
            mdd=latest_mc[row.strategy_id].mdd_p95 if row.strategy_id in latest_mc else None,
            sharpe=row.sharpe_mean,
            trade_count=None,
            started_at=row.created_at.isoformat() if row.created_at else None,
        )
        for row in wfa_rows
    ]

    return BacktestResponse(recent_runs=runs)
