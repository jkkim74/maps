"""MAPS FastAPI 애플리케이션 진입점."""

from __future__ import annotations

import logging
import secrets
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from maps.api.auth import auth_gate_middleware
from maps.api.auth import router as auth_router
from maps.common.db import Base, engine
from maps.common.logging_config import configure_logging
from maps.common.settings import get_settings

configure_logging()

# ── 라우터 임포트 ─────────────────────────────────────────────────────────────
from maps.api.dashboard import router as dashboard_router
from maps.api.strategies import router as strategies_router
from maps.api.market import router as market_router
from maps.api.candidates import router as candidates_router
from maps.api.orders import router as orders_router
from maps.api.risk import router as risk_router
from maps.api.backtest import router as backtest_router
from maps.api.robustness import router as robustness_router
from maps.api.trend_strength import router as trend_strength_router
from maps.api.research import router as research_router
from maps.api.wfa import router as wfa_router
from maps.api.cost_sensitivity import router as cost_sensitivity_router
from maps.api.live_monitor import router as live_monitor_router
from maps.api.data_quality import router as data_quality_router
from maps.api.ops_config import router as ops_config_router
from maps.api.scheduler import router as scheduler_router
from maps.api.stock_report import router as stock_report_router
from maps.api.mobile import router as mobile_router
from maps.api.trade_review import router as trade_review_router
from maps.api.stock_analysis import router as stock_analysis_router
from maps.api.analysis_picks import router as analysis_picks_router
from maps.api.telegram import router as telegram_router
from maps.api.daily_digest import router as daily_digest_router
from maps.api.blog import router as blog_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """앱 시작/종료 수명주기 핸들러."""
    import maps.common.models  # noqa: F401 — 모델 등록
    from maps.common.settings import (
        describe_trading_mode,
        get_settings,
        real_trading_unconfirmed,
    )
    from maps.ops.scheduler import (
        shutdown_operational_scheduler,
        start_operational_scheduler_if_enabled,
    )

    settings = get_settings()
    # 부팅 배너 — 현재 트레이딩 모드를 명시 (paper/real 오인 방지)
    logger.warning("=== MAPS 트레이딩 모드: %s ===", describe_trading_mode(settings))
    # 실거래 안전 가드 — REAL 거래가 활성인데 명시 확인이 없으면 기동 거부
    if real_trading_unconfirmed(settings):
        raise RuntimeError(
            "실거래(KIS_REAL_TRADING=true)가 주문 활성 상태로 설정됐으나 "
            "MAPS_CONFIRM_REAL_TRADING=true 확인이 없습니다. "
            "모의투자(paper)는 KIS_REAL_TRADING=false로 두세요. "
            "실거래가 의도라면 MAPS_CONFIRM_REAL_TRADING=true를 명시적으로 설정하십시오."
        )

    Base.metadata.create_all(bind=engine)
    start_operational_scheduler_if_enabled()
    logger.info("MAPS 서버 시작 완료")
    try:
        yield
    finally:
        shutdown_operational_scheduler()


app = FastAPI(
    title="MAPS — Market-Adaptive Profit Management System",
    version="0.2.0",
    description="검증 중심 자동매매 플랫폼",
    lifespan=_lifespan,
)

# ── 미들웨어 ──────────────────────────────────────────────────────────────────
# 마지막에 추가한 미들웨어가 가장 바깥에서 실행된다.
# 실행 순서(바깥→안): CORS → Session → AuthGate → 라우트
# (AuthGate가 request.session을 읽으므로 SessionMiddleware가 바깥에 있어야 한다.)
_settings = get_settings()
_session_secret = _settings.maps_session_secret_key or secrets.token_urlsafe(32)

app.add_middleware(BaseHTTPMiddleware, dispatch=auth_gate_middleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=_session_secret,
    max_age=_settings.maps_session_max_age,
    same_site="lax",
    https_only=_settings.maps_session_https_only,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 정적 파일 & 템플릿 ────────────────────────────────────────────────────────
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# ── API 라우터 등록 ───────────────────────────────────────────────────────────
app.include_router(dashboard_router)
app.include_router(strategies_router)
app.include_router(market_router)
app.include_router(candidates_router)
app.include_router(orders_router)
app.include_router(risk_router)
app.include_router(backtest_router)
app.include_router(robustness_router)
app.include_router(trend_strength_router)
app.include_router(research_router)
app.include_router(wfa_router)
app.include_router(cost_sensitivity_router)
app.include_router(live_monitor_router)
app.include_router(data_quality_router)
app.include_router(ops_config_router)
app.include_router(scheduler_router)
app.include_router(stock_report_router)
app.include_router(mobile_router)
app.include_router(trade_review_router)
app.include_router(stock_analysis_router)
app.include_router(analysis_picks_router)
app.include_router(telegram_router)
app.include_router(daily_digest_router)
app.include_router(blog_router)
app.include_router(auth_router)


# ── 헬스체크 ──────────────────────────────────────────────────────────────────

@app.get("/health")
async def health() -> dict[str, str]:
    """헬스체크 엔드포인트."""
    return {"status": "ok", "service": "maps", "version": "0.2.0"}


# ── 화면 라우트 (HTML 페이지) ──────────────────────────────────────────────────

_SCREEN_MAP = {
    "dashboard":        "대시보드 홈",
    "strategies":       "전략 관리",
    "market":           "장세/팩터",
    "candidates":       "종목 후보",
    "orders":           "주문/체결",
    "risk":             "리스크/모니터",
    "backtest":         "백테스트",
    "robustness":       "Trend Robustness",
    "trend-strength":   "TrendStrength Monitor",
    "research":         "Research Strategies",
    "wfa":              "Walk-Forward Report",
    "cost-sensitivity": "Cost Sensitivity",
    "live-monitor":     "Live Monitor",
    "data-quality":     "Data Quality",
    "ops-config":       "Ops Config",
    "trade-review":     "거래 리뷰",
    "stock-analysis":   "주식 종목 분석",
    "analysis-picks":   "분석 워치리스트",
    "blog":             "매매 기록",
    "maps-intro":       "MAPS 소개",
}


# 서버 시작 시각 기반 캐시 버스터 — 재시작 시마다 브라우저 캐시 무효화
_STATIC_VER = str(int(time.time()))


def _ctx(request: Request, screen: str, **extra) -> dict:
    return {
        "request": request,
        "screen": screen,
        "title": _SCREEN_MAP.get(screen, screen),
        "nav_items": list(_SCREEN_MAP.items()),
        "static_ver": _STATIC_VER,
        **extra,
    }


@app.get("/", response_class=HTMLResponse)
async def root(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "dashboard.html", _ctx(request, "dashboard"))


@app.get("/dashboard", response_class=HTMLResponse)
async def scr01(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "dashboard.html", _ctx(request, "dashboard"))


@app.get("/strategies", response_class=HTMLResponse)
async def scr02(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "strategies.html", _ctx(request, "strategies"))


@app.get("/market", response_class=HTMLResponse)
async def scr03(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "market.html", _ctx(request, "market"))


@app.get("/candidates", response_class=HTMLResponse)
async def scr04(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "candidates.html", _ctx(request, "candidates"))


@app.get("/orders", response_class=HTMLResponse)
async def scr05(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "orders.html", _ctx(request, "orders"))


@app.get("/risk", response_class=HTMLResponse)
async def scr06(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "risk.html", _ctx(request, "risk"))


@app.get("/backtest", response_class=HTMLResponse)
async def scr07(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "backtest.html", _ctx(request, "backtest"))


@app.get("/robustness", response_class=HTMLResponse)
async def scr08(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "robustness.html", _ctx(request, "robustness"))


@app.get("/trend-strength", response_class=HTMLResponse)
async def scr09(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "trend_strength.html", _ctx(request, "trend-strength"))


@app.get("/research", response_class=HTMLResponse)
async def scr10(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "research.html", _ctx(request, "research"))


@app.get("/wfa", response_class=HTMLResponse)
async def scr11(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "wfa.html", _ctx(request, "wfa"))


@app.get("/cost-sensitivity", response_class=HTMLResponse)
async def scr12(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "cost_sensitivity.html", _ctx(request, "cost-sensitivity"))


@app.get("/live-monitor", response_class=HTMLResponse)
async def scr13(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "live_monitor.html", _ctx(request, "live-monitor"))


@app.get("/data-quality", response_class=HTMLResponse)
async def scr14(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "data_quality.html", _ctx(request, "data-quality"))


@app.get("/ops-config", response_class=HTMLResponse)
async def scr15(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "ops_config.html", _ctx(request, "ops-config"))


@app.get("/stock-report", response_class=HTMLResponse)
async def stock_report_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "stock_report.html", _ctx(request, "stock-report"))


@app.get("/trade-review", response_class=HTMLResponse)
async def trade_review_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "trade_review.html", _ctx(request, "trade-review"))


@app.get("/stock-analysis", response_class=HTMLResponse)
async def stock_analysis_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "stock_analysis.html", _ctx(request, "stock-analysis"))


@app.get("/analysis-picks", response_class=HTMLResponse)
async def analysis_picks_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "analysis_picks.html", _ctx(request, "analysis-picks"))


@app.get("/blog", response_class=HTMLResponse)
async def blog_page(request: Request) -> HTMLResponse:
    """일일 매매 기록 — 저녁 cron이 생성한 Markdown 글 보기."""
    return templates.TemplateResponse(request, "blog.html", _ctx(request, "blog"))


@app.get("/maps-intro", response_class=HTMLResponse)
async def maps_intro_page(request: Request) -> HTMLResponse:
    """MAPS 소개 — 8개 전략 프로세스 인포그래픽 (standalone)."""
    return templates.TemplateResponse(request, "maps_strategy_infographic.html", _ctx(request, "maps-intro"))
