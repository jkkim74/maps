"""SCR-14 Data Quality API — P0."""

from __future__ import annotations

import datetime
import json

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from maps.api.deps import get_db
from maps.api.schemas import (
    DataQualityResponse,
    QualityHistoryPoint,
    RejectionReasonItem,
)
from maps.common.models import UniverseQualityLog

router = APIRouter(prefix="/api/v1/data-quality", tags=["SCR-14 Data Quality"])

_REASON_DESCRIPTIONS: dict[str, str] = {
    "low_turnover": "거래대금 하한 미달",
    "recently_listed": "상장 100일 미만",
    "trading_halted": "거래정지",
    "managed_stock": "관리종목 지정",
    "delisted": "상장폐지",
    "excluded_type": "스팩 자동 제외",
    "unadjusted_price": "수정주가 미반영",
    "delisted_before_ref": "기준일 이전 폐지 (백테스트)",
}


@router.get("", response_model=DataQualityResponse)
def get_data_quality(
    mode: str = Query(default="live"),
    ref_date: str = Query(default=""),
    db: Session = Depends(get_db),
) -> DataQualityResponse:
    """데이터 품질 현황을 반환한다."""
    target_date: datetime.date
    if ref_date:
        target_date = datetime.date.fromisoformat(ref_date)
    else:
        target_date = datetime.date.today()

    # 기준일 레코드 조회
    row = (
        db.query(UniverseQualityLog)
        .filter(
            UniverseQualityLog.ref_date == target_date,
            UniverseQualityLog.mode == mode,
        )
        .order_by(UniverseQualityLog.created_at.desc())
        .first()
    )

    # 최근 90일 이력
    since = target_date - datetime.timedelta(days=90)
    history_rows = (
        db.query(UniverseQualityLog)
        .filter(
            UniverseQualityLog.ref_date >= since,
            UniverseQualityLog.mode == mode,
        )
        .order_by(UniverseQualityLog.ref_date.asc())
        .all()
    )

    history = [
        QualityHistoryPoint(
            date=h.ref_date.isoformat(),
            rejection_ratio=h.rejection_ratio,
            total=h.total_candidates,
            kept=h.kept_count,
        )
        for h in history_rows
    ]

    if not row:
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
    total = row.total_candidates

    # rejected 집계 — UniverseQualityLog에 reasons JSON이 없으면 빈 목록
    reasons: list[RejectionReasonItem] = []

    return DataQualityResponse(
        ref_date=row.ref_date.isoformat(),
        mode=row.mode,
        total_candidates=total,
        kept_count=row.kept_count,
        rejected_count=rejected_count,
        rejection_ratio=round(row.rejection_ratio, 4),
        alert_sent=row.alert_sent,
        rejection_reasons=reasons,
        history_90d=history,
    )
