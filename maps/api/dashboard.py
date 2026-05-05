"""SCR-01 Dashboard API."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from maps.api.deps import get_db
from maps.api.schemas import AlertItem, DashboardResponse, StrategyContribution
from maps.common.constants import STRATEGY_GROUP_MAP
from maps.common.models import CandidateSnapshot, HistoricalOHLCV, KillSwitchLog, PromotionHistory

router = APIRouter(prefix="/api/v1/dashboard", tags=["SCR-01 Dashboard"])


@router.get("", response_model=DashboardResponse)
def get_dashboard(db: Session = Depends(get_db)) -> DashboardResponse:
    """Return dashboard summary data with sensible defaults for a fresh DB."""
    latest_promotions = _latest_promotions(db)
    strategy_ids = sorted(set(STRATEGY_GROUP_MAP) | set(latest_promotions))

    live_count = sum(
        1 for sid in strategy_ids if latest_promotions.get(sid) and latest_promotions[sid].to_stage == "live"
    )
    mock_count = sum(
        1
        for sid in strategy_ids
        if latest_promotions.get(sid)
        and latest_promotions[sid].to_stage in ("mock_candidate", "live_candidate")
    )

    contributions = [
        StrategyContribution(
            strategy_id=sid,
            name=sid,
            contribution_pct=0.0,
            stage=latest_promotions[sid].to_stage if sid in latest_promotions else "research",
        )
        for sid in strategy_ids
    ]

    latest_ohlcv_date = db.query(func.max(HistoricalOHLCV.date)).scalar()
    return DashboardResponse(
        total_assets=0.0,
        total_assets_mom_pct=0.0,
        ytd_cagr=0.0,
        current_mdd=0.0,
        sharpe_1y=0.0,
        active_strategies=live_count + mock_count,
        live_count=live_count,
        mock_count=mock_count,
        last_updated=latest_ohlcv_date.isoformat() if latest_ohlcv_date else "데이터 없음",
        contributions=contributions,
        alerts=_dashboard_alerts(db, latest_ohlcv_date),
    )


def _latest_promotions(db: Session) -> dict[str, PromotionHistory]:
    rows = db.query(PromotionHistory).order_by(PromotionHistory.evaluated_at.desc(), PromotionHistory.id.desc()).all()
    latest: dict[str, PromotionHistory] = {}
    for row in rows:
        if row.strategy_id not in latest:
            latest[row.strategy_id] = row
    return latest


def _dashboard_alerts(db: Session, latest_ohlcv_date) -> list[AlertItem]:
    kill_switch_rows = db.query(KillSwitchLog).order_by(KillSwitchLog.created_at.desc()).limit(5).all()
    if kill_switch_rows:
        return [
            AlertItem(
                level="WARN" if row.event_type == "trigger" else "INFO",
                message=f"Kill Switch [{row.event_type}] {row.strategy_id}: {row.reason}",
                timestamp=row.created_at.strftime("%H:%M") if row.created_at else "",
            )
            for row in kill_switch_rows
        ]

    alerts: list[AlertItem] = []
    ohlcv_rows = db.query(func.count(HistoricalOHLCV.id)).scalar() or 0
    candidate_rows = db.query(func.count(CandidateSnapshot.id)).scalar() or 0
    if latest_ohlcv_date:
        alerts.append(
            AlertItem(
                level="INFO",
                message=f"Historical OHLCV ready: {ohlcv_rows:,} rows as of {latest_ohlcv_date.isoformat()}",
                timestamp="",
            )
        )
    if candidate_rows:
        alerts.append(
            AlertItem(
                level="INFO",
                message=f"Candidate snapshots available: {candidate_rows:,} rows",
                timestamp="",
            )
        )
    if not alerts:
        alerts.append(
            AlertItem(
                level="INFO",
                message="No operational alerts yet. Run data collection or validation to populate dashboard metrics.",
                timestamp="",
            )
        )
    return alerts
