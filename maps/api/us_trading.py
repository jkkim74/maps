"""Administrator-only US paper controls; execution authority stays server-side."""
from __future__ import annotations

import datetime as dt
import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from maps.api.auth import current_identity
from maps.api.deps import get_db
from maps.api.schemas import USItemsResponse, USOrderRequest, USStatusResponse
from maps.common.exceptions import BrokerAdapterError, BrokerOrderUnknownError, ExecutionBlockedError
from maps.execution.broker_adapter import Order, OrderSide, OrderType
from maps.execution.safety import ExecutionContext

logger = logging.getLogger(__name__)


def administrator(request: Request):
    """Enforce role even when this router is mounted without the main middleware."""
    identity = current_identity(request)
    if not identity.is_admin:
        raise HTTPException(403, "administrator_required")
    return identity


router = APIRouter(prefix="/api/v1/us-trading", tags=["US paper trading"],
                   dependencies=[Depends(administrator)])


def get_service(db: Session = Depends(get_db)):
    """Build an account-scoped service using the request's database session."""
    from maps.ops.us_trading import make_service, close_service
    service = make_service(db)
    try:
        yield service
    finally:
        close_service(service)


def invoke(fn, *args, **kwargs):
    """Expose safe domain reasons without leaking broker responses or credentials."""
    try:
        return fn(*args, **kwargs)
    except ExecutionBlockedError as exc:
        raise HTTPException(409, {"code": exc.reason_code, "message": exc.reason_code}) from exc
    except BrokerOrderUnknownError as exc:
        raise HTTPException(409, {"code": "order_outcome_unknown", "message": "주문 결과 확인 중입니다. 다시 제출하지 마세요."}) from exc
    except BrokerAdapterError as exc:
        logger.warning("US broker operation unavailable: %s", type(exc).__name__)
        raise HTTPException(503, {"code": "broker_unavailable", "message": "브로커 조회를 완료하지 못했습니다."}) from exc


def manual_order(body: USOrderRequest, identity) -> tuple[Order, ExecutionContext]:
    """Translate the manual producer to the same contract used by future strategies."""
    order = Order(strategy_id="manual", ticker=body.ticker, side=OrderSide(body.side),
                  order_type=OrderType.LIMIT, quantity=body.quantity, limit_price=body.limit_price,
                  market="US", currency="USD", exchange=body.exchange)
    context = ExecutionContext(event_key=body.request_id, source="manual", actor=identity.username,
                               is_admin=True, instance_id="manual", sell_consent=body.sell_consent)
    return order, context


@router.get("/status", response_model=USStatusResponse)
def status(service=Depends(get_service)) -> dict:
    """Return saved health and safe configuration."""
    from maps.ops.us_trading import sync_running
    result = invoke(service.status)
    result["sync_running"] = sync_running()
    return result


@router.get("/account")
def account(service=Depends(get_service)) -> dict:
    """Return the last account observation, including its age."""
    return invoke(service.account)


@router.get("/positions", response_model=USItemsResponse)
def positions(service=Depends(get_service)) -> dict:
    """Show broker quantities separately from source-owned quantities."""
    return {"items": invoke(service.positions)}


@router.get("/quotes")
def quotes(ticker: str = Query(min_length=1, max_length=16),
           exchange: Literal["NASD", "NYSE", "AMEX"] = "NASD", service=Depends(get_service)) -> dict:
    """Return an allowlisted quote with explicit freshness evidence."""
    return invoke(service.quote, ticker, exchange)


@router.get("/orders", response_model=USItemsResponse)
def orders(ticker: str | None = None, side: Literal["buy", "sell"] | None = None,
           status: str | None = None, source: str | None = None,
           date_from: dt.date | None = None, date_to: dt.date | None = None,
           limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
           service=Depends(get_service)) -> dict:
    """Bounded account-local orders with US trading-date filters."""
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "invalid_date_range")
    return {"items": invoke(service.orders, ticker=ticker, side=side, status=status, source=source,
                            date_from=date_from, date_to=date_to, limit=limit, offset=offset)}


@router.post("/orders/preview")
def preview(body: USOrderRequest, identity=Depends(administrator), service=Depends(get_service)) -> dict:
    """Validate without reserving funds or submitting to the broker."""
    return invoke(service.preview, *manual_order(body, identity))


@router.post("/orders")
def submit(body: USOrderRequest, identity=Depends(administrator), service=Depends(get_service)) -> dict:
    """Revalidate current evidence and submit one durable order intent."""
    return invoke(service.submit, *manual_order(body, identity))


@router.get("/orders/{order_id}")
def order_detail(order_id: str, service=Depends(get_service)) -> dict:
    """Read one account-scoped order and its audit trail."""
    result = invoke(service.order_detail, order_id)
    if result is None:
        raise HTTPException(404, "order_not_found")
    return result


@router.post("/orders/{order_id}/cancel")
def cancel(order_id: str, identity=Depends(administrator), service=Depends(get_service)) -> dict:
    """Request cancellation without claiming that it has completed."""
    return invoke(service.cancel, order_id, identity.username)


@router.post("/sync")
def sync(identity=Depends(administrator)) -> dict:
    """Queue the same serialized background synchronization used by the scheduler."""
    from maps.ops.us_trading import request_sync
    return invoke(request_sync, identity.username)


@router.post("/entry-stop")
def entry_stop(identity=Depends(administrator), service=Depends(get_service)) -> dict:
    """Stop new buys without cancelling or liquidating existing orders or positions."""
    return invoke(service.set_entry_stop, True, identity.username)


@router.post("/entry-resume")
def entry_resume(identity=Depends(administrator), service=Depends(get_service)) -> dict:
    """Clear only the manual entry stop; other guards remain enforced."""
    return invoke(service.set_entry_stop, False, identity.username)


@router.get("/events", response_model=USItemsResponse)
def events(limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
           service=Depends(get_service)) -> dict:
    """Read redacted operational evidence for this account only."""
    return {"items": invoke(service.events, limit=limit, offset=offset)}
