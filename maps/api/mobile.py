"""Mobile client aggregate API.

The existing web application keeps using its screen-specific endpoints. The
hybrid client uses this compact endpoint to refresh its operational overview
with a single request.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from maps.api.dashboard import get_dashboard
from maps.api.deps import get_db
from maps.api.live_monitor import get_live_monitor
from maps.api.orders import get_orders
from maps.api.risk import get_risk
from maps.api.schemas import (
    AlertItem,
    DashboardResponse,
    LiveMonitorResponse,
    OrdersResponse,
    RiskResponse,
)

router = APIRouter(prefix="/api/v1/mobile", tags=["Mobile"])


class MobileSummaryResponse(BaseModel):
    server_time: str
    dashboard: DashboardResponse
    orders: OrdersResponse
    risk: RiskResponse
    live_monitor: LiveMonitorResponse
    alerts: list[AlertItem]


@router.get("/summary", response_model=MobileSummaryResponse)
def get_mobile_summary(db: Session = Depends(get_db)) -> MobileSummaryResponse:
    """Return the small operational payload needed by the hybrid app home."""
    dashboard = get_dashboard(db)
    return MobileSummaryResponse(
        server_time=dt.datetime.now(dt.timezone.utc).isoformat(),
        dashboard=dashboard,
        orders=get_orders(db),
        risk=get_risk(db),
        live_monitor=get_live_monitor(db),
        alerts=dashboard.alerts[:10],
    )
