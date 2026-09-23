"""Shared order-state helpers for candidate submission and preview."""

from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from maps.common.models import OrderLog
from maps.execution.broker_adapter import OrderSide, OrderStatus

_CLAIMED_CANDIDATE_STATUSES = frozenset({
    OrderStatus.PENDING.value,
    OrderStatus.PARTIALLY_FILLED.value,
    OrderStatus.FILLED.value,
    # 결과를 확정하지 못한 주문은 접수됐을 수 있으므로 점유로 본다(재매수 금지).
    OrderStatus.UNKNOWN.value,
})


def claimed_candidate_tickers(db: Session, *, since: dt.date) -> set[str]:
    """Return tickers already claimed by buy orders since a snapshot date."""
    cutoff = dt.datetime.combine(since, dt.time.min)
    rows = (
        db.query(OrderLog.ticker)
        .filter(OrderLog.created_at >= cutoff)
        .filter(OrderLog.side == OrderSide.BUY.value)
        .filter(OrderLog.status.in_(_CLAIMED_CANDIDATE_STATUSES))
        .all()
    )
    return {ticker for ticker, in rows}
