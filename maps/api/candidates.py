"""SCR-04 종목 후보 풀 API."""

from __future__ import annotations

import datetime

from fastapi import APIRouter, Query

from maps.api.schemas import CandidatesResponse

router = APIRouter(prefix="/api/v1/candidates", tags=["SCR-04 Candidates"])


@router.get("", response_model=CandidatesResponse)
def get_candidates(
    strategy_id: str = Query(default="pullback_v3"),
) -> CandidatesResponse:
    """전략별 종목 후보 풀을 반환한다.

    Phase 3에서 TrendStrength 모듈 연동 시 실데이터로 교체 예정.
    """
    return CandidatesResponse(
        strategy_id=strategy_id,
        universe_count=0,
        s5_excluded=0,
        missing_count=0,
        final_count=0,
        candidates=[],
        ref_date=datetime.date.today().isoformat(),
    )
