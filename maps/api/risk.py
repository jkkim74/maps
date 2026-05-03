"""SCR-06 리스크/모니터 API."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from maps.api.deps import get_db
from maps.api.schemas import RiskGaugeItem, RiskResponse
from maps.common.models import KillSwitchLog, MonteCarloSequenceResults

router = APIRouter(prefix="/api/v1/risk", tags=["SCR-06 Risk"])

_SHORT_TERM_LIMIT = 0.015   # RiskConfig.daily_loss_limit (1.5%)


@router.get("", response_model=RiskResponse)
def get_risk(db: Session = Depends(get_db)) -> RiskResponse:
    """전략군별 리스크 게이지 및 Kill Switch 현황을 반환한다."""
    # 전략별 최신 Kill Switch 이벤트
    recent_kills = (
        db.query(KillSwitchLog)
        .order_by(KillSwitchLog.created_at.desc(), KillSwitchLog.id.desc())
        .limit(200)
        .all()
    )
    latest_kill: dict[str, KillSwitchLog] = {}
    for row in recent_kills:
        if row.strategy_id and row.strategy_id not in latest_kill:
            latest_kill[row.strategy_id] = row

    active_kills = [r for r in latest_kill.values() if r.event_type in ("trigger", "approved")]
    position_count = len(active_kills)

    # 전략별 최신 Monte Carlo 결과로 게이지 구성
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

    gauges = [
        RiskGaugeItem(
            strategy_id=row.strategy_id,
            current_risk=row.mdd_p95,
            limit=row.mdd_limit,
            ratio=row.mdd_p95 / row.mdd_limit if row.mdd_limit > 0 else 0.0,
        )
        for row in latest_mc.values()
    ]

    # long_term_risk: 전략 중 가장 높은 MDD p95/limit 비율
    max_ratio = max((g.ratio for g in gauges), default=0.0)

    return RiskResponse(
        short_term_risk=0.0,        # 당일 PnL은 실시간 계좌 연동 필요 (Phase 5)
        short_term_limit=_SHORT_TERM_LIMIT,
        long_term_risk=max_ratio,
        long_term_limit=1.0,        # 1.0 = 한도 100% 도달
        max_exposure_pct=0.0,       # 실시간 포지션 연동 필요 (Phase 5)
        position_count=position_count,
        gauges=gauges,
        holdings=[],                # 실시간 포지션 연동 필요 (Phase 5)
    )
