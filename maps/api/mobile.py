"""Mobile client aggregate API.

The existing web application keeps using its screen-specific endpoints. The
hybrid client uses this compact endpoint to refresh its operational overview
with a single request.
"""

from __future__ import annotations

import datetime as dt
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from maps.api.auth import (
    check_credentials,
    make_mobile_token,
    verify_mobile_token,
)
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
from maps.common.models import DeviceToken
from maps.common.settings import get_settings
from maps.market.regime_history import latest_applied_regime

router = APIRouter(prefix="/api/v1/mobile", tags=["Mobile"])

logger = logging.getLogger(__name__)


class MobileLoginRequest(BaseModel):
    username: str
    password: str


class MobileLoginResponse(BaseModel):
    token: str
    username: str


class MobileRegime(BaseModel):
    """앱 홈에 표시할 압축 장세(시장 국면) 정보 — market_regime_log 최신 행 기반."""

    regime: str = "unknown"        # strong | mixed | weak | unknown (applied_regime)
    weekly_trend: str = "unknown"  # pass | fail | unknown
    vol_regime: str = "normal"     # low | normal | high
    breadth_pct: float | None = None
    up_count: int | None = None
    total_assets: int | None = None
    floor_applied: bool = False
    ref_date: str | None = None


class MobileSummaryResponse(BaseModel):
    server_time: str
    dashboard: DashboardResponse
    orders: OrdersResponse
    risk: RiskResponse
    live_monitor: LiveMonitorResponse
    alerts: list[AlertItem]
    regime: MobileRegime


def _mobile_regime(db: Session) -> MobileRegime:
    """최신 장세 판정 이력을 압축 응답으로 매핑한다(없거나 실패 시 unknown).

    /api/v1/market과 달리 실시간 재계산(pykrx/yfinance) 없이 스케줄러가 upsert한
    market_regime_log의 최근 applied_regime을 인덱스 1회 조회로 읽는다. 장세 조회
    실패가 summary 전체를 깨지 않도록 방어적으로 처리한다.
    """
    try:
        row = latest_applied_regime(db, dt.date.today())
    except Exception:  # noqa: BLE001 — 장세는 부가 정보, summary를 막지 않는다
        logger.warning("모바일 summary 장세 조회 실패", exc_info=True)
        return MobileRegime()
    if row is None:
        return MobileRegime()
    return MobileRegime(
        regime=row.applied_regime or "unknown",
        weekly_trend=row.weekly_trend or "unknown",
        vol_regime=row.vol_regime or "normal",
        breadth_pct=row.breadth_pct,
        up_count=row.up_count,
        total_assets=row.total_assets,
        floor_applied=bool(row.floor_applied),
        ref_date=row.ref_date.isoformat() if row.ref_date else None,
    )


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
        regime=_mobile_regime(db),
    )


# ── FCM 디바이스 토큰 등록 (Phase 4 네이티브 푸시) ──────────────────────────────
class DeviceTokenRequest(BaseModel):
    token: str
    platform: str = "android"  # android | ios | web


class DeviceTokenResponse(BaseModel):
    status: str
    id: int | None = None
    active: bool


def _mobile_username(request: Request) -> str | None:
    """Bearer 토큰에서 인증 사용자명을 추출한다(감사용, 없으면 None).

    인증 비활성 환경에서는 토큰이 없을 수 있으므로 best-effort로 처리한다.
    """
    header = request.headers.get("Authorization", "")
    prefix = "Bearer "
    if not header.startswith(prefix):
        return None
    token = header[len(prefix):].strip()
    if not token:
        return None
    return verify_mobile_token(token)


@router.post("/device-token", response_model=DeviceTokenResponse)
def register_device_token(
    body: DeviceTokenRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> DeviceTokenResponse:
    """FCM 등록 토큰을 등록/재활성화한다(토큰 기준 upsert, 앱 전용).

    같은 토큰이 이미 있으면 platform/username/last_seen_at을 갱신하고 active=True로
    되살린다(기기 재로그인). 새 토큰이면 새 행을 만든다.
    """
    token = (body.token or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="token이 비어 있습니다.")
    platform = (body.platform or "android").strip().lower()
    username = _mobile_username(request)
    now = dt.datetime.now(dt.timezone.utc)

    existing = db.query(DeviceToken).filter(DeviceToken.token == token).one_or_none()
    if existing is not None:
        existing.platform = platform
        existing.username = username
        existing.active = True
        existing.last_seen_at = now
        db.commit()
        db.refresh(existing)
        return DeviceTokenResponse(status="updated", id=existing.id, active=existing.active)

    device = DeviceToken(
        token=token, platform=platform, username=username, active=True, last_seen_at=now
    )
    db.add(device)
    db.commit()
    db.refresh(device)
    return DeviceTokenResponse(status="registered", id=device.id, active=device.active)


@router.delete("/device-token", response_model=DeviceTokenResponse)
def deregister_device_token(
    body: DeviceTokenRequest,
    db: Session = Depends(get_db),
) -> DeviceTokenResponse:
    """로그아웃/해지 시 토큰을 비활성화한다(행 삭제 대신 active=False).

    미등록 토큰이어도 200(멱등)으로 응답한다.
    """
    token = (body.token or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="token이 비어 있습니다.")
    existing = db.query(DeviceToken).filter(DeviceToken.token == token).one_or_none()
    if existing is None:
        return DeviceTokenResponse(status="not_found", id=None, active=False)
    existing.active = False
    existing.last_seen_at = dt.datetime.now(dt.timezone.utc)
    db.commit()
    db.refresh(existing)
    return DeviceTokenResponse(status="deregistered", id=existing.id, active=existing.active)


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
