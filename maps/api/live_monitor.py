"""SCR-13 Live Monitor API — P0."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from maps.api.deps import get_db
from maps.api.schemas import KillSwitchLogItem, LiveMonitorResponse
from maps.common.models import KillSwitchLog

router = APIRouter(prefix="/api/v1/live-monitor", tags=["SCR-13 Live Monitor"])


@router.get("", response_model=LiveMonitorResponse)
def get_live_monitor(db: Session = Depends(get_db)) -> LiveMonitorResponse:
    """실거래 모니터 현황을 반환한다."""
    recent = (
        db.query(KillSwitchLog)
        .order_by(KillSwitchLog.created_at.desc(), KillSwitchLog.id.desc())
        .limit(30)
        .all()
    )

    def _to_item(r: KillSwitchLog) -> KillSwitchLogItem:
        return KillSwitchLogItem(
            id=r.id,
            strategy_id=r.strategy_id,
            event_type=r.event_type,
            reason=r.reason,
            value=r.value,
            new_entry_blocked=r.new_entry_blocked,
            approved_by=r.approved_by,
            created_at=r.created_at.isoformat() if r.created_at else "",
        )

    # 전략별 최신 이벤트만 추출 (recent는 desc 정렬이므로 처음 등장이 최신)
    latest_per_strategy: dict[str | None, KillSwitchLog] = {}
    for r in recent:
        if r.strategy_id not in latest_per_strategy:
            latest_per_strategy[r.strategy_id] = r

    # 최신 이벤트가 "trigger"이고 미승인 → 청산 승인 대기
    pending_approvals = [
        _to_item(r) for r in latest_per_strategy.values()
        if r.event_type == "trigger" and not r.approved_by
    ]
    # 최신 이벤트가 "approved" → 청산 완료 후 Kill Switch 해제 대기
    pending_releases = [
        _to_item(r) for r in latest_per_strategy.values()
        if r.event_type == "approved"
    ]
    all_events = [_to_item(r) for r in recent]

    return LiveMonitorResponse(
        auto_response_active=True,
        pending_approval_count=len(pending_approvals),
        pending_release_count=len(pending_releases),
        actual_mdd=None,
        large_slip_actual=None,
        mid_small_slip_actual=None,
        consec_failures={},
        pending_approvals=pending_approvals,
        pending_releases=pending_releases,
        recent_events=all_events,
    )


class ApproveRequest(BaseModel):
    approved_by: str


@router.post("/{strategy_id}/approve-liquidation")
def approve_liquidation(
    strategy_id: str,
    body: ApproveRequest,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    """보유 포지션 청산을 승인한다 (신규 진입 차단은 유지).

    청산 승인 후 Kill Switch를 완전 해제하려면 /{strategy_id}/release 를 호출한다.
    """
    # 최신 이벤트 기준으로 상태 검증
    latest_row = (
        db.query(KillSwitchLog)
        .filter(KillSwitchLog.strategy_id == strategy_id)
        .order_by(KillSwitchLog.created_at.desc(), KillSwitchLog.id.desc())
        .first()
    )
    if not latest_row:
        raise HTTPException(status_code=404, detail=f"Kill Switch 이력 없음: {strategy_id}")
    if latest_row.event_type == "deactivate":
        raise HTTPException(
            status_code=409,
            detail=f"이미 해제된 Kill Switch: {strategy_id}",
        )
    if latest_row.event_type == "approved":
        raise HTTPException(
            status_code=409,
            detail=f"이미 청산 승인된 Kill Switch: {strategy_id}",
        )
    if latest_row.event_type != "trigger":
        raise HTTPException(status_code=404, detail=f"활성 Kill Switch 없음: {strategy_id}")

    # 기존 trigger 행에 승인자 기록 (감사용)
    latest_row.approved_by = body.approved_by
    # "approved" 이벤트 삽입: 청산 허용, 신규 진입은 여전히 차단
    db.add(
        KillSwitchLog(
            strategy_id=strategy_id,
            event_type="approved",
            reason=latest_row.reason,
            value=f"liquidation approved by {body.approved_by}",
            new_entry_blocked=True,
            approved_by=body.approved_by,
        )
    )
    db.commit()
    return {"status": "liquidation_approved", "strategy_id": strategy_id}


@router.post("/{strategy_id}/release")
def release_kill_switch(
    strategy_id: str,
    body: ApproveRequest,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    """Kill Switch를 완전 해제한다 (신규 진입 허용).

    청산 완료 후 리스크가 해소됐을 때 호출한다.
    approve-liquidation 없이도 직접 해제 가능 (긴급 해제 경로).
    """
    # 해제 대상 확인: 해당 전략의 최신 이벤트가 trigger 또는 approved여야 함
    latest_row = (
        db.query(KillSwitchLog)
        .filter(KillSwitchLog.strategy_id == strategy_id)
        .order_by(KillSwitchLog.created_at.desc(), KillSwitchLog.id.desc())
        .first()
    )
    if not latest_row:
        raise HTTPException(
            status_code=404,
            detail=f"Kill Switch 이력 없음: {strategy_id}",
        )
    if latest_row.event_type == "deactivate":
        raise HTTPException(
            status_code=409,
            detail=f"이미 해제된 Kill Switch: {strategy_id}",
        )
    if latest_row.event_type not in ("trigger", "approved"):
        raise HTTPException(
            status_code=404,
            detail=f"해제 대상 Kill Switch 없음: {strategy_id}",
        )
    active_row = latest_row

    # "deactivate" 이벤트 삽입 → RiskManager.is_new_entry_blocked() DB 동기화에 사용됨
    db.add(
        KillSwitchLog(
            strategy_id=strategy_id,
            event_type="deactivate",
            reason=active_row.reason,
            value=f"kill switch released by {body.approved_by}",
            new_entry_blocked=False,
            approved_by=body.approved_by,
        )
    )
    db.commit()
    return {"status": "released", "strategy_id": strategy_id}
