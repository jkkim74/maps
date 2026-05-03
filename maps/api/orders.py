"""SCR-05 주문/체결 API."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from maps.api.deps import get_db
from maps.api.schemas import (
    FillItem,
    OrderQueueItem,
    OrdersResponse,
    SlippageStats,
)
from maps.common.models import KillSwitchLog, OrderLog

router = APIRouter(prefix="/api/v1/orders", tags=["SCR-05 Orders"])


@router.get("", response_model=OrdersResponse)
def get_orders(db: Session = Depends(get_db)) -> OrdersResponse:
    """주문 큐 및 금일 체결 이력을 반환한다."""
    import datetime

    today = datetime.date.today()
    today_start = datetime.datetime.combine(today, datetime.time.min)

    rows = (
        db.query(OrderLog)
        .filter(OrderLog.created_at >= today_start)
        .order_by(OrderLog.created_at.desc())
        .limit(100)
        .all()
    )

    pending = [
        OrderQueueItem(
            order_id=r.order_id,
            strategy_id=r.strategy_id,
            ticker=r.ticker,
            side=r.side,
            qty=r.qty,
            order_price=r.order_price,
            status=r.status,
            created_at=r.created_at.isoformat() if r.created_at else "",
        )
        for r in rows
        if r.status in ("pending", "PENDING")
    ]
    fills = [
        FillItem(
            order_id=r.order_id,
            ticker=r.ticker,
            side=r.side,
            fill_price=r.fill_price,
            fill_qty=r.fill_qty,
            status=r.status,
            created_at=r.created_at.isoformat() if r.created_at else "",
        )
        for r in rows
        if r.status in ("filled", "FILLED", "partially_filled", "PARTIAL")
    ]

    # 활성 Kill Switch(trigger/approved)가 하나라도 있으면 자동 주문 비활성
    ks_recent = (
        db.query(KillSwitchLog)
        .order_by(KillSwitchLog.created_at.desc(), KillSwitchLog.id.desc())
        .limit(200)
        .all()
    )
    latest_ks: dict[str, KillSwitchLog] = {}
    for ks in ks_recent:
        if ks.strategy_id and ks.strategy_id not in latest_ks:
            latest_ks[ks.strategy_id] = ks
    auto_order_active = not any(
        ks.event_type in ("trigger", "approved") for ks in latest_ks.values()
    )

    return OrdersResponse(
        auto_order_active=auto_order_active,
        pending=pending,
        fills_today=fills,
        slippage=SlippageStats(
            large_cap_actual=None,
            large_cap_assumed=0.0005,
            mid_small_actual=None,
            mid_small_assumed=0.0015,
        ),
    )
