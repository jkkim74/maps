"""SCR-14 Data Quality API."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from maps.api.deps import get_db
from maps.api.schemas import DataQualityResponse, QualityHistoryPoint, RejectionReasonItem
from maps.common.models import UniverseQualityLog

router = APIRouter(prefix="/api/v1/data-quality", tags=["SCR-14 Data Quality"])


@router.get("", response_model=DataQualityResponse)
def get_data_quality(
    mode: str = Query(default="live"),
    ref_date: str = Query(default=""),
    db: Session = Depends(get_db),
) -> DataQualityResponse:
    """Return the latest data-quality audit summary for the requested mode."""
    target_date = _target_date(db, mode, ref_date)
    row = _latest_row_for_date(db, mode, target_date)
    history = _history(db, mode, target_date)

    if row is None:
        return DataQualityResponse(
            ref_date=target_date.isoformat(),
            mode=mode,
            total_candidates=0,
            kept_count=0,
            rejected_count=0,
            rejection_ratio=0.0,
            alert_sent=False,
            rejection_reasons=[],
            history_90d=history,
        )

    rejected_count = row.excluded_count
    reasons = []
    if rejected_count:
        reasons.append(
            RejectionReasonItem(
                reason_code="quality_filter_rejected",
                description="Rejected by universe quality filters",
                count=rejected_count,
                ratio=rejected_count / row.total_candidates if row.total_candidates else 0.0,
            )
        )

    return DataQualityResponse(
        ref_date=row.ref_date.isoformat(),
        mode=row.mode,
        total_candidates=row.total_candidates,
        kept_count=row.kept_count,
        rejected_count=rejected_count,
        rejection_ratio=round(row.rejection_ratio, 4),
        alert_sent=row.alert_sent,
        rejection_reasons=reasons,
        history_90d=history,
    )


def _target_date(db: Session, mode: str, ref_date: str) -> dt.date:
    if ref_date:
        return dt.date.fromisoformat(ref_date)
    latest = (
        db.query(func.max(UniverseQualityLog.ref_date))
        .filter(UniverseQualityLog.mode == mode, UniverseQualityLog.total_candidates > 0)
        .scalar()
    )
    if latest:
        return latest
    latest = (
        db.query(func.max(UniverseQualityLog.ref_date))
        .filter(UniverseQualityLog.mode == mode)
        .scalar()
    )
    return latest or dt.date.today()


def _latest_row_for_date(db: Session, mode: str, target_date: dt.date) -> UniverseQualityLog | None:
    return (
        db.query(UniverseQualityLog)
        .filter(UniverseQualityLog.ref_date == target_date, UniverseQualityLog.mode == mode)
        .order_by(UniverseQualityLog.created_at.desc(), UniverseQualityLog.id.desc())
        .first()
    )


def _history(db: Session, mode: str, target_date: dt.date) -> list[QualityHistoryPoint]:
    since = target_date - dt.timedelta(days=90)
    rows = (
        db.query(UniverseQualityLog)
        .filter(
            UniverseQualityLog.ref_date >= since,
            UniverseQualityLog.mode == mode,
            UniverseQualityLog.total_candidates > 0,
        )
        .order_by(UniverseQualityLog.ref_date.asc(), UniverseQualityLog.created_at.asc(), UniverseQualityLog.id.asc())
        .all()
    )
    latest_by_date: dict[dt.date, UniverseQualityLog] = {}
    for row in rows:
        latest_by_date[row.ref_date] = row

    return [
        QualityHistoryPoint(
            date=row.ref_date.isoformat(),
            rejection_ratio=row.rejection_ratio,
            total=row.total_candidates,
            kept=row.kept_count,
        )
        for _date, row in sorted(latest_by_date.items())
    ]
