"""Mobile client aggregate API.

The existing web application keeps using its screen-specific endpoints. The
hybrid client uses this compact endpoint to refresh its operational overview
with a single request.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from maps.api.auth import check_credentials, make_mobile_token
from maps.api.dashboard import get_daily_pnl, get_dashboard
from maps.api.deps import get_db
from maps.api.live_monitor import get_live_monitor
from maps.api.orders import get_orders
from maps.api.risk import get_risk
from maps.api.schemas import (
    AlertItem,
    DashboardResponse,
    LiveMonitorResponse,
    OrdersResponse,
    PortfolioHistoryPoint,
    PortfolioHistoryResponse,
    RiskResponse,
)
from maps.common.settings import get_settings

router = APIRouter(prefix="/api/v1/mobile", tags=["Mobile"])


class MobileLoginRequest(BaseModel):
    username: str
    password: str


class MobileLoginResponse(BaseModel):
    token: str
    username: str


class MobileSummaryResponse(BaseModel):
    server_time: str
    dashboard: DashboardResponse
    orders: OrdersResponse
    risk: RiskResponse
    live_monitor: LiveMonitorResponse
    alerts: list[AlertItem]


@router.post("/login", response_model=MobileLoginResponse)
def mobile_login(body: MobileLoginRequest) -> MobileLoginResponse:
    """공유 비밀번호를 검증하고 Bearer 토큰을 발급한다(앱 전용).

    인증 비활성 환경에서도 올바른 자격증명이면 토큰을 발급하며, 자격증명이
    틀리거나 비밀번호 미설정이면 401. 앱은 401 응답으로 로그인 실패를 구분한다.
    """
    settings = get_settings()
    if not check_credentials(settings, body.username, body.password):
        raise HTTPException(status_code=401, detail="아이디 또는 비밀번호가 올바르지 않습니다.")
    username = body.username or settings.maps_auth_username
    return MobileLoginResponse(token=make_mobile_token(username, settings), username=username)


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


@router.get("/portfolio-history", response_model=PortfolioHistoryResponse)
def get_mobile_portfolio_history(
    days: int = Query(default=30, ge=1, le=365, description="조회 일수 (최대 365일)"),
    db: Session = Depends(get_db),
) -> PortfolioHistoryResponse:
    """앱 추이 차트용 포트폴리오 자산 시계열을 반환한다.

    별도의 데이터를 생성하지 않고, 대시보드 일별 손익(`get_daily_pnl`)이 이미
    `PortfolioSnapshot(source='broker')`의 일별 총 자산으로부터 계산한 시계열을
    그대로 재사용한다. 스냅샷이 없으면 빈 시계열을 반환한다(값을 지어내지 않음).
    """
    pnl = get_daily_pnl(days=days, db=db)
    points = [
        PortfolioHistoryPoint(
            date=item.date,
            total_value=item.total_assets,
            pnl_pct=item.pnl_pct,
        )
        for item in pnl.items
    ]
    return PortfolioHistoryResponse(
        days=pnl.days,
        cumulative_pct=pnl.cumulative_pct,
        points=points,
    )
