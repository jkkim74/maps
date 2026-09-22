"""SCR-03 장세/팩터 분석 API."""

from __future__ import annotations

import datetime as dt
import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from maps.api.deps import get_db
from maps.api.schemas import AssetTrend, MarketResponse
from maps.common.models import MarketRegimeLog
from maps.common.settings import get_settings
from maps.market.regime import CombinedWeeklyProvider, MarketRegimeAnalyzer, create_regime_analyzer
from maps.market.regime_history import latest_applied_regime

router = APIRouter(prefix="/api/v1/market", tags=["SCR-03 Market"])
logger = logging.getLogger(__name__)

# 하위 호환: 테스트가 market._CombinedWeeklyProvider 를 패치하므로 별칭 유지
_CombinedWeeklyProvider = CombinedWeeklyProvider

_KST = dt.timezone(dt.timedelta(hours=9))
# 이력이 이보다 오래되면 실시간 계산으로 폴백한다. 연휴(최대 3일)를 덮는다.
_REGIME_LOG_MAX_AGE_DAYS = 3


@router.get("", response_model=MarketResponse)
def get_market(db: Session = Depends(get_db)) -> MarketResponse:
    """현재 장세 및 팩터 분석 데이터를 반환한다.

    스케줄러가 매 거래일 남기는 `market_regime_log` 최근 행을 우선 쓴다 — 히스테리시스를
    거친 `applied_regime` 이 매매 판정의 정본이고, 실시간 계산은 KRX·yfinance 시세 10건과
    시장 내부지표 집계로 요청당 6~28초가 걸렸다(2026-09-22 실측). 행이 없거나 컬럼 추가
    이전 행(`asset_trends` NULL)이면 예전처럼 실시간으로 계산한다.
    """
    settings = get_settings()
    today = dt.datetime.now(_KST).date()
    row = latest_applied_regime(db, today, max_age_days=_REGIME_LOG_MAX_AGE_DAYS)
    if row is not None and row.asset_trends is not None:
        return _from_regime_log(row, settings)
    return _from_live_analysis(settings)


def _from_regime_log(row: MarketRegimeLog, settings) -> MarketResponse:  # noqa: ANN001
    """이력 행 하나로 응답을 만든다. 외부 호출·무거운 계산이 없다."""
    scores = row.factor_scores or {}
    updated = row.updated_at
    if updated is not None and updated.tzinfo is None:
        updated = updated.replace(tzinfo=dt.timezone.utc)
    return MarketResponse(
        regime=row.applied_regime,
        weekly_trend=row.weekly_trend,
        limit_ratio=row.entry_limit_ratio or 0.0,
        kospi_ts=row.kospi_ts or 0.0,
        assets=[
            AssetTrend(
                name=str(item.get("name", "")),
                direction=str(item.get("direction", "flat")),
                value=float(item["value"]) if item.get("value") is not None else 0.0,
            )
            for item in row.asset_trends or []
        ],
        updated_at=updated.isoformat() if updated is not None else None,
        legacy_regime=row.raw_regime,
        composite_regime=row.composite_regime or row.applied_regime,
        market_mode=row.market_mode,
        price_trend_score=scores.get("price_trend"),
        volatility_score=scores.get("volatility"),
        liquidity_score=scores.get("liquidity"),
        foreign_fx_score=scores.get("foreign_fx"),
        psychology_score=scores.get("psychology"),
        final_market_score=row.final_market_score,
        policy_regime=row.policy_regime or row.applied_regime,
        score_coverage_ratio=row.score_coverage_ratio,
        score_status=row.score_status,
        score_ready=row.score_ready,
        measured_factors=list(row.measured_factors or []),
        missing_factors=list(row.missing_factors or []),
        factor_sources=dict(row.factor_sources or {}),
        contrarian_entry_limit_ratio=settings.maps_contrarian_max_entry_ratio,
        reason=row.score_reason,
        source="regime_log",
        ref_date=row.ref_date.isoformat(),
    )


def _from_live_analysis(settings) -> MarketResponse:  # noqa: ANN001
    """이력이 없을 때의 실시간 계산 경로(로컬·신규 설치용)."""
    result = create_regime_analyzer(settings).analyze()
    composite = result.composite
    market_mode = result.market_mode(
        contrarian_enabled=settings.maps_contrarian_accumulation_enabled
    )
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
        legacy_regime=composite.legacy_regime if composite else result.regime.value,
        composite_regime=composite.composite_regime if composite else result.regime.value,
        market_mode=market_mode.value,
        price_trend_score=composite.price_trend_score if composite else None,
        volatility_score=composite.volatility_score if composite else None,
        liquidity_score=composite.liquidity_score if composite else None,
        foreign_fx_score=composite.foreign_fx_score if composite else None,
        psychology_score=composite.psychology_score if composite else None,
        final_market_score=composite.final_market_score if composite else None,
        policy_regime=composite.policy_regime if composite else result.regime.value,
        score_coverage_ratio=composite.coverage_ratio if composite else 0.0,
        score_status=composite.score_status if composite else "unavailable",
        score_ready=composite.score_ready if composite else False,
        measured_factors=list(composite.measured_factors) if composite else [],
        missing_factors=list(composite.missing_factors) if composite else [],
        factor_sources=dict(composite.factor_sources) if composite else {},
        contrarian_entry_limit_ratio=settings.maps_contrarian_max_entry_ratio,
        reason=composite.reason if composite else "legacy market regime mode",
        source="live",
        ref_date=None,
    )
