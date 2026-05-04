"""SCR-03 장세/팩터 분석 API."""

from __future__ import annotations

import datetime
import logging

from fastapi import APIRouter

from maps.api.schemas import AssetTrend, MarketResponse
from maps.market.regime import MarketRegimeAnalyzer

router = APIRouter(prefix="/api/v1/market", tags=["SCR-03 Market"])
logger = logging.getLogger(__name__)

_INDEX_TICKERS = {
    "KOSPI": "1001",
    "KOSDAQ": "2001",
}


@router.get("", response_model=MarketResponse)
def get_market() -> MarketResponse:
    """현재 장세 및 팩터 분석 데이터를 반환한다.

    KRX에서 제공되는 국내 지수는 실 주봉 종가로 계산한다.
    해외 지수/원자재는 별도 데이터 공급원 연동 전까지 값 없음으로
    표시하되, 화면 렌더링은 동일한 응답 구조를 사용한다.
    """
    result = MarketRegimeAnalyzer(_KRXIndexWeeklyProvider()).analyze()
    return MarketResponse(
        regime=result.regime.value,
        weekly_trend=result.weekly_trend.value,
        limit_ratio=result.entry_limit_ratio,
        kospi_ts=result.kospi_ts,
        assets=[
            AssetTrend(name=item.name, direction=item.direction, value=item.value)
            for item in result.assets
        ],
        updated_at=result.evaluated_at.isoformat(),
    )


class _KRXIndexWeeklyProvider:
    """pykrx 기반 국내 지수 주봉 종가 제공자."""

    def get_weekly_closes(self, asset_name: str, n_weeks: int) -> list[float]:
        ticker = _INDEX_TICKERS.get(asset_name)
        if not ticker:
            return []
        try:
            from pykrx import stock
        except ImportError:
            logger.warning("pykrx is not installed; market index data unavailable")
            return []

        end = datetime.date.today()
        start = end - datetime.timedelta(days=max(n_weeks * 10, 90))
        try:
            df = stock.get_index_ohlcv_by_date(
                start.strftime("%Y%m%d"),
                end.strftime("%Y%m%d"),
                ticker,
                freq="w",
            )
        except Exception as exc:
            logger.warning("KRX index weekly data unavailable [%s]: %s", asset_name, exc)
            return []

        close_col = "종가" if "종가" in df.columns else "Close" if "Close" in df.columns else None
        if close_col is None:
            logger.warning("KRX index close column missing [%s]: %s", asset_name, list(df.columns))
            return []
        return [float(value) for value in df[close_col].dropna().tail(n_weeks).tolist()]
