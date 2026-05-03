"""SCR-03 장세/팩터 분석 API."""

from __future__ import annotations

import datetime

from fastapi import APIRouter

from maps.api.schemas import AssetTrend, MarketResponse

router = APIRouter(prefix="/api/v1/market", tags=["SCR-03 Market"])


@router.get("", response_model=MarketResponse)
def get_market() -> MarketResponse:
    """현재 장세 및 팩터 분석 데이터를 반환한다.

    Phase 3에서 MarketRegime 모듈 연동 시 실데이터로 교체 예정.
    """
    return MarketResponse(
        regime="mixed",
        weekly_trend="pass",
        limit_ratio=0.5,
        kospi_ts=None,
        assets=[
            AssetTrend(name="KOSPI", direction="up", value=None),
            AssetTrend(name="KOSDAQ", direction="up", value=None),
            AssetTrend(name="S&P 500", direction="up", value=None),
            AssetTrend(name="NASDAQ", direction="up", value=None),
            AssetTrend(name="USD/KRW", direction="down", value=None),
            AssetTrend(name="금", direction="up", value=None),
            AssetTrend(name="WTI", direction="flat", value=None),
            AssetTrend(name="구리", direction="down", value=None),
        ],
        updated_at=datetime.datetime.now().isoformat(),
    )
