"""SCR-03 장세/팩터 분석 API."""

from __future__ import annotations

import logging

from fastapi import APIRouter

from maps.api.schemas import AssetTrend, MarketResponse
from maps.common.settings import get_settings
from maps.market.regime import CombinedWeeklyProvider, MarketRegimeAnalyzer, create_regime_analyzer

router = APIRouter(prefix="/api/v1/market", tags=["SCR-03 Market"])
logger = logging.getLogger(__name__)

# 하위 호환: 테스트가 market._CombinedWeeklyProvider 를 패치하므로 별칭 유지
_CombinedWeeklyProvider = CombinedWeeklyProvider


@router.get("", response_model=MarketResponse)
def get_market() -> MarketResponse:
    """현재 장세 및 팩터 분석 데이터를 반환한다."""
    result = create_regime_analyzer(get_settings()).analyze()
    return MarketResponse(
        regime=result.regime.value,
        weekly_trend=result.weekly_trend.value,
        limit_ratio=result.entry_limit_ratio,
        kospi_ts=result.kospi_ts or 0.0,
        assets=[
            AssetTrend(name=item.name, direction=item.direction, value=item.value or 0.0)
            for item in result.assets
        ],
        updated_at=result.evaluated_at.isoformat(),
    )
