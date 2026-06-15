"""Stock Analysis API — 종목 종합 분석 엔드포인트."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from maps.common.settings import get_settings

router = APIRouter(prefix="/api/v1/stock-analysis", tags=["Stock Analysis"])


class AnalyzeRequest(BaseModel):
    """분석 요청 바디."""
    ticker: str


@router.post("/analyze")
async def analyze_stock(req: AnalyzeRequest) -> dict[str, Any]:
    """종목명 또는 6자리 종목코드를 받아 종합 분석 결과를 반환한다.

    pykrx·DART 호출이 블로킹이므로 스레드 풀에서 실행한다.
    """
    from maps.stock_analysis.analyzer import analyze

    ticker = req.ticker.strip()
    if not ticker:
        raise HTTPException(status_code=400, detail="ticker가 비어 있습니다.")

    settings = get_settings()
    dart_key = settings.dart_api_key

    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(None, analyze, ticker, dart_key)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"분석 중 오류: {e}")

    return result
