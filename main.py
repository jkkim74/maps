"""MAPS FastAPI 애플리케이션 진입점."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from maps.common.db import Base, engine
from maps.common.logging_config import configure_logging

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

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """앱 시작/종료 수명주기 핸들러."""
    import maps.common.models  # noqa: F401 — 모델 등록
    from maps.ops.scheduler import (
        shutdown_operational_scheduler,
        start_operational_scheduler_if_enabled,
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
}


def _ctx(request: Request, screen: str, **extra) -> dict:
    return {
        "request": request,
        "screen": screen,
        "title": _SCREEN_MAP.get(screen, screen),
        "nav_items": list(_SCREEN_MAP.items()),
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
