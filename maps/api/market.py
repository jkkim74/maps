"""SCR-03 장세/팩터 분석 API."""

from __future__ import annotations

import datetime
import logging

import pandas as pd
from fastapi import APIRouter

from maps.api.schemas import AssetTrend, MarketResponse
from maps.market.regime import MarketRegimeAnalyzer

router = APIRouter(prefix="/api/v1/market", tags=["SCR-03 Market"])
logger = logging.getLogger(__name__)

# 국내 지수: pykrx 티커
_KRX_TICKERS = {
    "KOSPI": "1001",
    "KOSDAQ": "2001",
}

# 해외 지수/원자재: yfinance 티커
_YF_TICKERS = {
    "S&P 500": "^GSPC",
    "NASDAQ":  "^IXIC",
    "USD/KRW": "KRW=X",
    "금":      "GC=F",
    "WTI":     "CL=F",
    "구리":    "HG=F",
}


@router.get("", response_model=MarketResponse)
def get_market() -> MarketResponse:
    """현재 장세 및 팩터 분석 데이터를 반환한다."""
    result = MarketRegimeAnalyzer(_CombinedWeeklyProvider()).analyze()
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


class _CombinedWeeklyProvider:
    """pykrx(국내) + yfinance(해외) 통합 주봉 종가 제공자."""

    def get_weekly_closes(self, asset_name: str, n_weeks: int) -> list[float]:
        if asset_name in _KRX_TICKERS:
            return self._from_krx(asset_name, n_weeks)
        if asset_name in _YF_TICKERS:
            return self._from_yfinance(asset_name, n_weeks)
        return []

    # ── 국내 지수 (pykrx) ────────────────────────────────────────────────────

    def _from_krx(self, asset_name: str, n_weeks: int) -> list[float]:
        ticker = _KRX_TICKERS[asset_name]
        try:
            from pykrx import stock
        except ImportError:
            logger.warning("pykrx not installed")
            return []

        end = datetime.date.today()
        start = end - datetime.timedelta(days=max(n_weeks * 10, 120))
        prev_raise = logging.raiseExceptions
        prev_level = logging.root.manager.disable
        try:
            logging.raiseExceptions = False
            logging.disable(logging.CRITICAL)
            # pykrx 1.2.8은 freq='w' 미지원 — 일봉 조회 후 주봉 리샘플링
            df = stock.get_index_ohlcv_by_date(
                start.strftime("%Y%m%d"),
                end.strftime("%Y%m%d"),
                ticker,
                freq="d",
            )
        except Exception as exc:
            logger.warning("KRX daily data unavailable [%s]: %s", asset_name, exc)
            return []
        finally:
            logging.disable(prev_level)
            logging.raiseExceptions = prev_raise

        if df is None or df.empty:
            logger.warning("KRX returned empty dataframe [%s]", asset_name)
            return []

        close_col = "종가" if "종가" in df.columns else "Close" if "Close" in df.columns else None
        if close_col is None:
            logger.warning("KRX close column missing [%s]: cols=%s", asset_name, list(df.columns))
            return []

        weekly = df[close_col].resample("W").last().dropna()
        return [float(v) for v in weekly.tail(n_weeks).tolist()]

    # ── 해외 지수/원자재 (yfinance) ──────────────────────────────────────────

    def _from_yfinance(self, asset_name: str, n_weeks: int) -> list[float]:
        ticker = _YF_TICKERS[asset_name]
        try:
            import yfinance as yf
        except ImportError:
            logger.warning("yfinance not installed; overseas data unavailable")
            return []

        end = datetime.date.today() + datetime.timedelta(days=1)
        start = end - datetime.timedelta(days=max(n_weeks * 10, 120))
        try:
            df = yf.download(
                ticker,
                start=start.strftime("%Y-%m-%d"),
                end=end.strftime("%Y-%m-%d"),
                interval="1d",
                progress=False,
                auto_adjust=True,
            )
        except Exception as exc:
            logger.warning("yfinance data unavailable [%s]: %s", asset_name, exc)
            return []

        if df is None or df.empty:
            logger.warning("yfinance returned empty dataframe [%s]", asset_name)
            return []

        close = df["Close"] if "Close" in df.columns else df.iloc[:, 0]
        if hasattr(close, "squeeze"):
            close = close.squeeze()
        weekly = close.resample("W").last().dropna()
        return [float(v) for v in weekly.tail(n_weeks).tolist()]
