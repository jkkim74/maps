"""Stock Analysis API — 종목 종합 분석 엔드포인트."""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from maps.ai.trade_planner import AITradePlan, AITradePlanner, StockTradeFacts
from maps.api.auth import current_identity, load_user
from maps.api.deps import DbDep
from maps.api.schemas import (
    StockAnalysisHistoryDetail,
    StockAnalysisHistoryListItem,
    StockAnalysisHistoryListResponse,
    StockAnalysisPriceOverlay,
    StockTradePlanRequest,
    StockTradePlanResponse,
)
from maps.common import db as db_module
from maps.common.exceptions import AIScoringError
from maps.common.models import StockAnalysisHistory
from maps.common.settings import get_settings
from maps.common.user_prefs import analysis_quota_exceeded
from maps.market.trading_rules import round_to_krx_tick, round_up_krx_price
from maps.stock_analysis.history import (
    CurrentPriceUnavailable,
    refresh_analysis_price,
    save_analysis_history,
    save_analysis_history_with_new_session,
)

router = APIRouter(prefix="/api/v1/stock-analysis", tags=["Stock Analysis"])
logger = logging.getLogger(__name__)


class AnalyzeRequest(BaseModel):
    """분석 요청 바디."""
    ticker: str


_MANUAL_MESSAGE = "AI 매매계획을 사용할 수 없어 수동 입력이 필요합니다."


def _owner_scope(request: Request, db: Session):
    """(요청자 계정, 조회를 자기 것으로 제한해야 하는지)를 반환한다.

    관리자와 인증 비활성 환경은 전체를 본다. 일반 사용자는 자기 소유만 본다.
    """
    identity = current_identity(request)
    if identity.is_admin:
        return None, False
    return load_user(db, identity.username), True


def _may_read(request: Request, db: Session, row: StockAnalysisHistory) -> bool:
    """이 분석 이력을 요청자가 볼 수 있는지 판단한다."""
    user, scoped = _owner_scope(request, db)
    if not scoped:
        return True
    return user is not None and row.owner_user_id == user.id


def _guard_analysis_quota(request: Request, db: Session) -> int | None:
    """일일 분석 한도를 확인하고 소유자 id 를 돌려준다.

    한도 초과면 **AI를 호출하기 전에** 429로 끊는다 — Bedrock 비용은 호출 순간 발생한다.
    """
    user, _scoped = _owner_scope(request, db)
    if user is None:
        return None
    if analysis_quota_exceeded(db, user):
        raise HTTPException(
            status_code=429, detail="오늘 사용할 수 있는 종목분석 횟수를 모두 썼습니다."
        )
    return user.id


def _manual_trade_plan(
    *, recommendation: str = "WATCH", rationale: str = ""
) -> StockTradePlanResponse:
    return StockTradePlanResponse(
        recommendation=recommendation,
        rationale=rationale,
        source="MANUAL_REQUIRED",
        message=_MANUAL_MESSAGE,
    )


def _trade_plan_request(result: dict[str, Any]) -> StockTradePlanRequest:
    """Map one collected analysis result into the structured planner contract."""
    technical = result.get("기술적분석") or {}
    valuation = result.get("밸류에이션") or {}
    averages = technical.get("이동평균선") or {}
    return StockTradePlanRequest(
        ticker=result.get("종목코드") or "",
        name=result.get("종목명") or result.get("종목코드") or "",
        market=result.get("시장") or "KOSPI",
        ref_date=technical.get("기준일") or "",
        current_price=technical.get("현재가"),
        high_52w=technical.get("52주_고가"),
        low_52w=technical.get("52주_저가"),
        ma20=averages.get("MA20"),
        ma60=averages.get("MA60"),
        ma120=averages.get("MA120"),
        rsi14=technical.get("RSI14"),
        macd=technical.get("MACD"),
        macd_signal=technical.get("MACD_signal"),
        per=valuation.get("PER"),
        pbr=valuation.get("PBR"),
        bps=valuation.get("BPS"),
    )


def generate_trade_plan(req: StockTradePlanRequest) -> StockTradePlanResponse:
    """Return one normalized structured plan for analysis and UI consumers."""
    planner = AITradePlanner.from_settings()
    if not planner.is_configured:
        return _manual_trade_plan()

    facts = StockTradeFacts.model_validate(req.model_dump())
    try:
        plan = planner.plan(facts)
    except AIScoringError:
        return _manual_trade_plan()

    normalized_payload = {
        "recommendation": plan.recommendation,
        "entries": [
            round_up_krx_price(price, market=facts.market) for price in plan.entries
        ],
        "target": round_to_krx_tick(plan.target, market=facts.market),
        "stop": round_to_krx_tick(plan.stop, market=facts.market),
        "rationale": plan.rationale,
    }
    try:
        normalized = AITradePlan.from_payload(normalized_payload)
    except AIScoringError:
        return _manual_trade_plan()
    return StockTradePlanResponse(
        recommendation=normalized.recommendation,
        entries=list(normalized.entries),
        target=normalized.target,
        stop=normalized.stop,
        rationale=normalized.rationale,
        source="AI",
    )


@router.post("/trade-plan", response_model=StockTradePlanResponse)
async def create_trade_plan(req: StockTradePlanRequest) -> StockTradePlanResponse:
    """Return the same normalized plan used by the stock-analysis stream."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, generate_trade_plan, req)


def _history_list_item(row: StockAnalysisHistory) -> StockAnalysisHistoryListItem:
    """Map one history ORM row to the lightweight response contract."""
    created_at = row.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=datetime.timezone.utc)
    refreshed_at = row.price_refreshed_at
    if refreshed_at is not None and refreshed_at.tzinfo is None:
        refreshed_at = refreshed_at.replace(tzinfo=datetime.timezone.utc)
    return StockAnalysisHistoryListItem(
        id=row.id,
        created_at=created_at,
        ticker=row.ticker,
        name=row.name,
        market=row.market,
        ref_date=row.ref_date,
        recommendation=row.recommendation,
        analyzed_price=row.analyzed_price,
        latest_price=row.latest_price,
        latest_price_source=row.latest_price_source,
        price_refreshed_at=refreshed_at,
    )


@router.get("/history", response_model=StockAnalysisHistoryListResponse)
def list_analysis_history(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = DbDep,
) -> StockAnalysisHistoryListResponse:
    """Return completed analyses newest first without heavy detail fields.

    일반 사용자에게는 **자기 분석만** 보인다. 소유자가 없는 기존 행은 운영자 데이터다.
    """
    user, scoped = _owner_scope(request, db)
    query = db.query(StockAnalysisHistory)
    if scoped:
        owner_id = user.id if user is not None else None
        query = query.filter(StockAnalysisHistory.owner_user_id == owner_id)
    total = query.count()
    rows = (
        query.order_by(
            StockAnalysisHistory.created_at.desc(),
            StockAnalysisHistory.id.desc(),
        )
        .offset(offset)
        .limit(limit)
        .all()
    )
    return StockAnalysisHistoryListResponse(
        total=total,
        items=[_history_list_item(row) for row in rows],
    )


@router.get("/history/{history_id}", response_model=StockAnalysisHistoryDetail)
def get_analysis_history(
    history_id: int, request: Request, db: Session = DbDep
) -> StockAnalysisHistoryDetail:
    """Return one stored analysis without refreshing or mutating it."""
    row = db.get(StockAnalysisHistory, history_id)
    if row is None or not _may_read(request, db, row):
        raise HTTPException(status_code=404, detail="분석 이력을 찾을 수 없습니다.")
    return StockAnalysisHistoryDetail(
        **_history_list_item(row).model_dump(),
        snapshot=row.snapshot,
        narrative=row.narrative,
        trade_plan=row.trade_plan,
        latest_reference_close=row.latest_reference_close,
    )


@router.post(
    "/history/{history_id}/refresh-price",
    response_model=StockAnalysisPriceOverlay,
)
def refresh_history_price(
    history_id: int, request: Request, db: Session = DbDep
) -> StockAnalysisPriceOverlay:
    """Refresh only the mutable current-price overlay for a saved analysis."""
    row = db.get(StockAnalysisHistory, history_id)
    if row is None or not _may_read(request, db, row):
        raise HTTPException(status_code=404, detail="분석 이력을 찾을 수 없습니다.")
    try:
        return refresh_analysis_price(db, row)
    except CurrentPriceUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/analyze")
async def analyze_stock(
    req: AnalyzeRequest, request: Request, db: Session = DbDep
) -> dict[str, Any]:
    """종목명 또는 6자리 종목코드를 받아 종합 분석 결과를 반환한다 (단일 응답)."""
    from maps.stock_analysis.analyzer import analyze

    ticker = req.ticker.strip()
    if not ticker:
        raise HTTPException(status_code=400, detail="ticker가 비어 있습니다.")
    owner_user_id = _guard_analysis_quota(request, db)

    settings = get_settings()
    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(None, analyze, ticker, settings.dart_api_key)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"분석 중 오류: {e}")

    try:
        trade_plan = generate_trade_plan(_trade_plan_request(result))
    except (TypeError, ValueError):
        trade_plan = _manual_trade_plan()
    history = save_analysis_history(
        db,
        result=result,
        narrative="",
        trade_plan=trade_plan.model_dump(mode="json"),
        owner_user_id=owner_user_id,
    )
    return {**result, "history_id": history.id}


@router.get("/stream")
async def analyze_stream(ticker: str, request: Request) -> StreamingResponse:
    """Server-Sent Events로 분석 진행률을 실시간 스트리밍한다.

    각 이벤트 형식:
        {"step": "단계명", "pct": 0-100, "done": false}
    완료 이벤트:
        {"step": "분석 완료", "pct": 100, "done": true, "data": {...}}
    오류 이벤트:
        {"step": "오류", "pct": 0, "done": true, "error": "메시지"}
    """
    from maps.stock_analysis.analyzer import analyze

    ticker = ticker.strip()
    if not ticker:
        raise HTTPException(status_code=400, detail="ticker가 비어 있습니다.")

    # 스트림을 열기 전에 한도를 확인한다 — 열고 나면 429를 돌려줄 수 없다.
    with db_module.SessionLocal() as quota_db:
        owner_user_id = _guard_analysis_quota(request, quota_db)

    settings = get_settings()
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    def _progress(step: str, pct: int) -> None:
        asyncio.run_coroutine_threadsafe(
            queue.put({"step": step, "pct": pct, "done": False}),
            loop,
        )

    def _run() -> None:
        try:
            result = analyze(ticker, settings.dart_api_key, progress_callback=_progress)
            try:
                trade_plan = generate_trade_plan(_trade_plan_request(result))
            except (TypeError, ValueError):
                trade_plan = _manual_trade_plan()
            trade_plan_payload = trade_plan.model_dump(mode="json")
            narrative_parts: list[str] = []

            if settings.aws_access_key_id:
                _progress("AI 종합분석 시작 (Claude via Bedrock)…", 98)
                try:
                    from maps.stock_analysis.analyzer import stream_llm_analysis
                    for chunk in stream_llm_analysis(
                        result,
                        aws_access_key_id=settings.aws_access_key_id,
                        aws_secret_access_key=settings.aws_secret_access_key,
                        aws_region=settings.aws_region,
                        model_id=settings.aws_bedrock_model_id,
                        trade_plan=trade_plan_payload,
                    ):
                        narrative_parts.append(chunk)
                        asyncio.run_coroutine_threadsafe(
                            queue.put({"analysis_chunk": chunk, "done": False}),
                            loop,
                        )
                except Exception as llm_err:
                    error_marker = f"\n\n[AI 분석 오류: {llm_err}]"
                    narrative_parts.append(error_marker)
                    asyncio.run_coroutine_threadsafe(
                        queue.put({"analysis_chunk": error_marker, "done": False}),
                        loop,
                    )

            history_id: int | None = None
            history_error: str | None = None
            try:
                history_id = save_analysis_history_with_new_session(
                    result,
                    "".join(narrative_parts),
                    trade_plan_payload,
                    owner_user_id=owner_user_id,
                )
            except Exception as exc:
                history_error = str(exc)
                logger.exception("Stock analysis history save failed [%s]", ticker)

            asyncio.run_coroutine_threadsafe(
                queue.put({
                    "step": "분석 완료",
                    "pct": 100,
                    "done": True,
                    "data": result,
                    "trade_plan": trade_plan_payload,
                    "history_id": history_id,
                    "history_error": history_error,
                }),
                loop,
            )
        except ValueError as e:
            asyncio.run_coroutine_threadsafe(
                queue.put({"step": "오류", "pct": 0, "done": True, "error": str(e)}),
                loop,
            )
        except Exception as e:
            asyncio.run_coroutine_threadsafe(
                queue.put({"step": "오류", "pct": 0, "done": True, "error": f"분석 실패: {e}"}),
                loop,
            )

    loop.run_in_executor(None, _run)

    async def _generate():
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=600.0)
            except asyncio.TimeoutError:
                yield "data: {\"step\": \"타임아웃\", \"pct\": 0, \"done\": true, \"error\": \"분석 시간 초과 (10분)\"}\n\n"
                break
            yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
            if item.get("done"):
                break

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # nginx 버퍼링 비활성화
            "Connection": "keep-alive",
        },
    )
