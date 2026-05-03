"""SCR-01 대시보드 홈 API."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from maps.api.deps import get_db
from maps.api.schemas import AlertItem, DashboardResponse, StrategyContribution
from maps.common.models import KillSwitchLog, PromotionHistory

router = APIRouter(prefix="/api/v1/dashboard", tags=["SCR-01 Dashboard"])


@router.get("", response_model=DashboardResponse)
def get_dashboard(db: Session = Depends(get_db)) -> DashboardResponse:
    """대시보드 홈 집계 데이터를 반환한다."""
    # 최근 승격 이력에서 활성 전략 파악
    promotions = (
        db.query(PromotionHistory)
        .order_by(PromotionHistory.evaluated_at.desc())
        .limit(20)
        .all()
    )

    # 전략별 최신 단계 집계
    latest: dict[str, PromotionHistory] = {}
    for p in promotions:
        if p.strategy_id not in latest:
            latest[p.strategy_id] = p

    live_count = sum(1 for p in latest.values() if p.to_stage == "live")
    mock_count = sum(1 for p in latest.values() if p.to_stage in ("mock_candidate", "live_candidate"))
    active_count = live_count + mock_count

    contributions = [
        StrategyContribution(
            strategy_id=sid,
            name=sid,
            contribution_pct=0.0,
            stage=p.to_stage,
        )
        for sid, p in latest.items()
    ]

    # 최근 Kill Switch 이벤트를 알림으로 변환
    ks_events = (
        db.query(KillSwitchLog)
        .order_by(KillSwitchLog.created_at.desc())
        .limit(5)
        .all()
    )
    alerts = [
        AlertItem(
            level="WARN" if e.event_type == "trigger" else "INFO",
            message=f"Kill Switch [{e.event_type}] {e.strategy_id}: {e.reason}",
            timestamp=e.created_at.strftime("%H:%M") if e.created_at else "",
        )
        for e in ks_events
    ]

    return DashboardResponse(
        total_assets=0.0,
        total_assets_mom_pct=0.0,
        ytd_cagr=0.0,
        current_mdd=0.0,
        sharpe_1y=0.0,
        active_strategies=active_count,
        live_count=live_count,
        mock_count=mock_count,
        last_updated="데이터 없음",
        contributions=contributions,
        alerts=alerts,
    )
