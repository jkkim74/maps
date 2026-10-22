"""SCR-05 주문/체결 API."""

from __future__ import annotations

import datetime as dt
import math

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from maps.api.deps import get_db
from maps.api.schemas import (
    FillItem,
    OrderPreviewResponse,
    OrderQueueItem,
    OrdersResponse,
    ReconciliationResponse,
    ReconciliationSideStat,
    ReconciliationUnfilledItem,
    SlippageStats,
)
from maps.common.models import OrderLog, SecurityMetadata
from maps.common.settings import get_settings
from maps.execution.readiness import execution_readiness, utc_iso
from maps.execution.safety import account_key, execution_environment, utcnow
from maps.ops.order_preview import build_order_preview
from maps.ops.reconciliation import build_reconciliation

router = APIRouter(prefix="/api/v1/orders", tags=["SCR-05 Orders"])


@router.get("/reconciliation", response_model=ReconciliationResponse)
def get_reconciliation(
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_db),
) -> ReconciliationResponse:
    """최근 N일 주문 정산 리포트 — 제출/체결/만료 집계 + 미체결 도달가능성 진단."""
    settings = get_settings()
    summary = build_reconciliation(db, days=days, account=account_key(settings),
                                   environment=execution_environment(settings))
    return ReconciliationResponse(
        start_kst=summary.start_kst,
        end_kst=summary.end_kst,
        total_orders=summary.total_orders,
        by_side=[
            ReconciliationSideStat(
                side=s.side,
                submitted=s.submitted,
                filled=s.filled,
                partially_filled=s.partially_filled,
                expired=s.expired,
                rejected=s.rejected,
                fill_rate=s.fill_rate,
            )
            for s in summary.by_side
        ],
        unfilled=[
            ReconciliationUnfilledItem(
                kst_date=u.kst_date,
                strategy_id=u.strategy_id,
                ticker=u.ticker,
                side=u.side,
                status=u.status,
                qty=u.qty,
                order_price=u.order_price,
                day_high=u.day_high,
                day_low=u.day_low,
                day_close=u.day_close,
                reachable=u.reachable,
            )
            for u in summary.unfilled
        ],
    )


@router.get("", response_model=OrdersResponse)
def get_orders(db: Session = Depends(get_db)) -> OrdersResponse:
    """Return scoped open orders and cumulative fills of orders created today KST."""
    settings, now = get_settings(), utcnow()
    day = (now + dt.timedelta(hours=9)).date()
    start = dt.datetime.combine(day, dt.time.min) - dt.timedelta(hours=9)
    end = start + dt.timedelta(days=1)
    scoped = db.query(OrderLog).filter(OrderLog.account_key == account_key(settings),
                                       OrderLog.environment == execution_environment(settings))
    pending_rows = (scoped.filter(func.lower(OrderLog.status).in_(
        ("pending", "partially_filled", "partial", "unknown")),
        OrderLog.qty > OrderLog.fill_qty).order_by(OrderLog.created_at.desc(), OrderLog.id.desc()).all())
    # Today is the order-created day, not a fill-event date (not stored here).
    fill_rows = (scoped.filter(OrderLog.created_at >= start, OrderLog.created_at < end,
                               OrderLog.fill_qty > 0)
                 .order_by(OrderLog.created_at.desc(), OrderLog.id.desc()).all())
    expired_rows = (scoped.filter(OrderLog.created_at >= start - dt.timedelta(days=7),
                                  OrderLog.created_at < end, func.lower(OrderLog.status) == "expired")
                    .order_by(OrderLog.created_at.desc(), OrderLog.id.desc()).limit(100).all())
    tickers = {r.ticker for r in pending_rows + fill_rows + expired_rows}
    names = {m.ticker: m.name for m in db.query(SecurityMetadata).filter(
        SecurityMetadata.ticker.in_(tickers)).all()} if tickers else {}

    def queue_item(row: OrderLog) -> OrderQueueItem:
        """Keep requested, confirmed filled and unresolved remaining shares distinct."""
        return OrderQueueItem(order_id=row.order_id, strategy_id=row.strategy_id,
            ticker=row.ticker, name=names.get(row.ticker, ""), side=row.side,
            qty=row.qty, fill_qty=row.fill_qty, remaining_qty=max(0, row.qty - row.fill_qty),
            order_price=row.order_price, status=row.status, created_at=utc_iso(row.created_at) or "")

    samples = [abs(r.fill_price - r.order_price) / r.order_price for r in fill_rows
               if r.fill_price is not None and r.order_price is not None
               and math.isfinite(r.fill_price) and math.isfinite(r.order_price)
               and r.order_price > 0 and r.fill_price > 0]
    return OrdersResponse(
        **execution_readiness(db, settings, now),
        pending=[queue_item(r) for r in pending_rows],
        fills_today=[FillItem(order_id=r.order_id, ticker=r.ticker, name=names.get(r.ticker, ""),
            side=r.side, fill_price=r.fill_price, fill_qty=r.fill_qty, status=r.status,
            created_at=utc_iso(r.created_at) or "", strategy_id=r.strategy_id, qty=r.qty,
            order_price=r.order_price) for r in fill_rows],
        expired=[queue_item(r) for r in expired_rows],
        slippage=SlippageStats(actual=sum(samples) / len(samples) if samples else None,
            sample_count=len(samples), large_cap_actual=None, mid_small_actual=None,
            large_cap_assumed=0.0005, mid_small_assumed=0.0015),
    )


@router.get("/preview", response_model=OrderPreviewResponse)
def get_order_preview(db: Session = Depends(get_db)) -> OrderPreviewResponse:
    """다음 거래일 예정 주문 미리보기를 반환한다.

    실제 브로커 호출 없이 DB CandidateSnapshot + PortfolioSnapshot + settings 기반으로
    스케줄러와 동일한 로직을 시뮬레이션한다.
    """
    return build_order_preview(db, get_settings())
