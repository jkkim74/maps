"""SCR-05 주문/체결 API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
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
from maps.common.models import KillSwitchLog, OrderLog, SecurityMetadata
from maps.common.settings import get_settings
from maps.ops.order_preview import build_order_preview
from maps.ops.reconciliation import build_reconciliation

router = APIRouter(prefix="/api/v1/orders", tags=["SCR-05 Orders"])


@router.get("/reconciliation", response_model=ReconciliationResponse)
def get_reconciliation(
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_db),
) -> ReconciliationResponse:
    """최근 N일 주문 정산 리포트 — 제출/체결/만료 집계 + 미체결 도달가능성 진단."""
    summary = build_reconciliation(db, days=days)
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
    """주문 큐 및 금일 체결 이력을 반환한다."""
    import datetime

    import zoneinfo
    _KST = zoneinfo.ZoneInfo("Asia/Seoul")
    today_kst = datetime.datetime.now(_KST).date()
    # KST 자정 기준으로 "오늘" 산정 (8:55 KST 주문 = 23:55 UTC 전날 포함)
    kst_today_start = datetime.datetime.combine(today_kst, datetime.time.min) - datetime.timedelta(hours=9)
    week_start = kst_today_start - datetime.timedelta(days=7)

    rows = (
        db.query(OrderLog)
        .filter(OrderLog.created_at >= week_start)
        .order_by(OrderLog.created_at.desc())
        .limit(100)
        .all()
    )

    # 종목명 일괄 조회
    tickers = {r.ticker for r in rows}
    name_map: dict[str, str] = {}
    if tickers:
        name_map = {
            m.ticker: m.name
            for m in db.query(SecurityMetadata).filter(SecurityMetadata.ticker.in_(tickers)).all()
        }

    pending = [
        OrderQueueItem(
            order_id=r.order_id,
            strategy_id=r.strategy_id,
            ticker=r.ticker,
            name=name_map.get(r.ticker, ""),
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
            name=name_map.get(r.ticker, ""),
            side=r.side,
            fill_price=r.fill_price,
            fill_qty=r.fill_qty,
            status=r.status,
            created_at=r.created_at.isoformat() if r.created_at else "",
            strategy_id=r.strategy_id,
            qty=r.qty,
        )
        for r in rows
        if r.status in ("filled", "FILLED", "partially_filled", "PARTIAL")
        and r.created_at is not None
        and r.created_at >= kst_today_start
    ]
    expired_orders = [
        OrderQueueItem(
            order_id=r.order_id,
            strategy_id=r.strategy_id,
            ticker=r.ticker,
            name=name_map.get(r.ticker, ""),
            side=r.side,
            qty=r.qty,
            order_price=r.order_price,
            status=r.status,
            created_at=r.created_at.isoformat() if r.created_at else "",
        )
        for r in rows
        if r.status in ("expired", "EXPIRED")
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

    # 실제 슬리피지: 금일 체결 주문의 fill_price vs order_price 평균 괴리율
    # (대형/중소형 분류는 종목 시가총액 연동 후 세분화 예정 — 현재는 통합 평균)
    fill_rows_with_price = [
        r for r in rows
        if r.status in ("filled", "FILLED", "partially_filled", "PARTIAL")
        and r.fill_price is not None
        and r.order_price is not None
        and r.order_price > 0
    ]
    actual_slip: float | None = None
    if fill_rows_with_price:
        actual_slip = sum(
            abs(r.fill_price - r.order_price) / r.order_price  # type: ignore[operator]
            for r in fill_rows_with_price
        ) / len(fill_rows_with_price)

    return OrdersResponse(
        auto_order_active=auto_order_active,
        pending=pending,
        fills_today=fills,
        expired=expired_orders,
        slippage=SlippageStats(
            large_cap_actual=actual_slip if actual_slip is not None else 0.0005,
            large_cap_assumed=0.0005,
            mid_small_actual=actual_slip if actual_slip is not None else 0.0015,
            mid_small_assumed=0.0015,
        ),
    )


@router.get("/preview", response_model=OrderPreviewResponse)
def get_order_preview(db: Session = Depends(get_db)) -> OrderPreviewResponse:
    """다음 거래일 예정 주문 미리보기를 반환한다.

    실제 브로커 호출 없이 DB CandidateSnapshot + PortfolioSnapshot + settings 기반으로
    스케줄러와 동일한 로직을 시뮬레이션한다.
    """
    return build_order_preview(db, get_settings())
