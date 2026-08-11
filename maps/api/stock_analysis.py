"""Stock Analysis API — 종목 종합 분석 엔드포인트."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from maps.ai.trade_planner import AITradePlan, AITradePlanner, StockTradeFacts
from maps.api.schemas import StockTradePlanRequest, StockTradePlanResponse
from maps.common.exceptions import AIScoringError
from maps.common.settings import get_settings
from maps.market.trading_rules import round_to_krx_tick, round_up_krx_price

router = APIRouter(prefix="/api/v1/stock-analysis", tags=["Stock Analysis"])


class AnalyzeRequest(BaseModel):
    """분석 요청 바디."""
    ticker: str


_MANUAL_MESSAGE = "AI 매매계획을 사용할 수 없어 수동 입력이 필요합니다."


def _manual_trade_plan(
    *, recommendation: str = "WATCH", rationale: str = ""
) -> StockTradePlanResponse:
    return StockTradePlanResponse(
        recommendation=recommendation,
        rationale=rationale,
        source="MANUAL_REQUIRED",
        message=_MANUAL_MESSAGE,
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


@router.post("/analyze")
async def analyze_stock(req: AnalyzeRequest) -> dict[str, Any]:
    """종목명 또는 6자리 종목코드를 받아 종합 분석 결과를 반환한다 (단일 응답)."""
    from maps.stock_analysis.analyzer import analyze

    ticker = req.ticker.strip()
    if not ticker:
        raise HTTPException(status_code=400, detail="ticker가 비어 있습니다.")

    settings = get_settings()
    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(None, analyze, ticker, settings.dart_api_key)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"분석 중 오류: {e}")

    return result


@router.get("/stream")
async def analyze_stream(ticker: str) -> StreamingResponse:
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
                    ):
                        asyncio.run_coroutine_threadsafe(
                            queue.put({"analysis_chunk": chunk, "done": False}),
                            loop,
                        )
                except Exception as llm_err:
                    asyncio.run_coroutine_threadsafe(
                        queue.put({"analysis_chunk": f"\n\n[AI 분석 오류: {llm_err}]", "done": False}),
                        loop,
                    )

            asyncio.run_coroutine_threadsafe(
                queue.put({"step": "분석 완료", "pct": 100, "done": True, "data": result}),
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
