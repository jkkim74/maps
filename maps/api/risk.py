"""SCR-06 리스크/모니터 API."""

from __future__ import annotations

from fastapi import APIRouter

from maps.api.schemas import RiskResponse

router = APIRouter(prefix="/api/v1/risk", tags=["SCR-06 Risk"])


@router.get("", response_model=RiskResponse)
def get_risk() -> RiskResponse:
    """전략군별 리스크 게이지 및 보유 현황을 반환한다.

    Phase 4 이후 RiskManager 인스턴스 상태와 연동 예정.
    """
    return RiskResponse(
        short_term_risk=0.0,
        short_term_limit=0.02,
        long_term_risk=0.0,
        long_term_limit=0.03,
        max_exposure_pct=0.0,
        position_count=0,
        gauges=[],
        holdings=[],
    )
