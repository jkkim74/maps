"""SCR-09 TrendStrength Monitor API."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from maps.api.deps import get_db
from maps.api.schemas import TrendStrengthResponse
from maps.common.constants import TREND_STRENGTH_BUCKETS
from maps.common.models import HistoricalOHLCV
from maps.data.ohlcv_repo import HistoricalOHLCVRepository
from maps.indicator.trend_strength import TrendStrengthCalculator

router = APIRouter(prefix="/api/v1/trend-strength", tags=["SCR-09 TrendStrength"])


@router.get("", response_model=TrendStrengthResponse)
def get_trend_strength(
    ref_date: str = Query(default=""),
    min_bars: int = Query(default=60, ge=20, le=756),
    limit: int = Query(default=500, ge=1, le=3000),
    db: Session = Depends(get_db),
) -> TrendStrengthResponse:
    """Return current TrendStrength bucket distribution from stored OHLCV."""
    if ref_date:
        as_of = dt.date.fromisoformat(ref_date)
    else:
        latest_date = db.query(func.max(HistoricalOHLCV.date)).scalar()
        as_of = latest_date or dt.date.today()

    repo = HistoricalOHLCVRepository(db)
    tickers = repo.list_tickers_on_date(as_of, limit=limit)

    lookback_start = as_of - dt.timedelta(days=min_bars * 3)
    ohlcv_map = repo.recent_dataframes(
        tickers,
        start=lookback_start,
        end=as_of,
        bars=min_bars,
    )
    result = TrendStrengthCalculator(min_bars=min_bars).score_universe(ohlcv_map, as_of)
    scored_count = len(result.scores)

    buckets = []
    counts = result.bucket_counts
    for bucket in TREND_STRENGTH_BUCKETS:
        count = counts.get(bucket["grade"], 0)
        buckets.append(
            {
                "grade": bucket["grade"],
                "label": bucket["label"],
                "count": count,
                "ratio": count / scored_count if scored_count else 0.0,
            }
        )

    return TrendStrengthResponse(
        ref_date=as_of.isoformat(),
        universe_count=len(tickers),
        missing_count=len(result.missing),
        buckets=buckets,
        history_30d=[],
    )
