"""SCR-04 종목 후보 풀 API."""

from __future__ import annotations

import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from maps.api.deps import get_db
from maps.api.schemas import CandidatesResponse
from maps.api.schemas import CandidateItem
from maps.common.models import CandidateSnapshot, UniverseQualityLog

router = APIRouter(prefix="/api/v1/candidates", tags=["SCR-04 Candidates"])


@router.get("", response_model=CandidatesResponse)
def get_candidates(
    strategy_id: str = Query(default="pullback_v3"),
    db: Session = Depends(get_db),
) -> CandidatesResponse:
    """전략별 최신 후보 스냅샷을 반환한다."""
    latest_date = (
        db.query(func.max(CandidateSnapshot.ref_date))
        .filter(CandidateSnapshot.strategy_id == strategy_id)
        .scalar()
    )
    if latest_date is None:
        today = datetime.date.today()
        return CandidatesResponse(
            strategy_id=strategy_id,
            universe_count=0,
            s5_excluded=0,
            missing_count=0,
            final_count=0,
            candidates=[],
            ref_date=today.isoformat(),
        )

    final_count = (
        db.query(func.count(CandidateSnapshot.id))
        .filter(
            CandidateSnapshot.strategy_id == strategy_id,
            CandidateSnapshot.ref_date == latest_date,
        )
        .scalar()
        or 0
    )
    rows = (
        db.query(CandidateSnapshot)
        .filter(
            CandidateSnapshot.strategy_id == strategy_id,
            CandidateSnapshot.ref_date == latest_date,
        )
        .order_by(CandidateSnapshot.final_score.desc(), CandidateSnapshot.ticker.asc())
        .limit(200)
        .all()
    )
    quality = (
        db.query(UniverseQualityLog)
        .filter(UniverseQualityLog.ref_date == latest_date, UniverseQualityLog.mode == "live")
        .order_by(UniverseQualityLog.created_at.desc())
        .first()
    )
    return CandidatesResponse(
        strategy_id=strategy_id,
        universe_count=quality.total_candidates if quality else len(rows),
        s5_excluded=0,
        missing_count=quality.excluded_count if quality else 0,
        final_count=final_count,
        candidates=[
            CandidateItem(
                ticker=row.ticker,
                name=row.name,
                factor_score=row.factor_score,
                trend_strength=row.trend_strength,
                ts_bucket=row.ts_bucket,
                final_score=row.final_score,
                weekly_pass=row.weekly_pass,
                estimated_qty=row.estimated_qty,
            )
            for row in rows
        ],
        ref_date=latest_date.isoformat(),
    )
