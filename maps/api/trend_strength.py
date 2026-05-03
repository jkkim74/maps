"""SCR-09 TrendStrength Monitor API."""

from __future__ import annotations

import datetime

from fastapi import APIRouter, Query

from maps.api.schemas import TrendStrengthResponse

router = APIRouter(prefix="/api/v1/trend-strength", tags=["SCR-09 TrendStrength"])


@router.get("", response_model=TrendStrengthResponse)
def get_trend_strength(
    ref_date: str = Query(default=""),
) -> TrendStrengthResponse:
    """유니버스 추세 강도 분포를 반환한다.

    Phase 3에서 TrendStrength 모듈 연동 시 실데이터로 교체 예정.
    """
    today = ref_date or datetime.date.today().isoformat()
    return TrendStrengthResponse(
        ref_date=today,
        universe_count=0,
        missing_count=0,
        buckets=[],
        history_30d=[],
    )
