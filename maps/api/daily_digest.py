"""일일 다이제스트 API — 블로그 생성기 입력이자 검증·디버깅 창구."""

from __future__ import annotations

import datetime as dt
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from maps.api.deps import get_db
from maps.api.schemas import DailyDigest
from maps.common.settings import get_settings
from maps.ops.daily_digest import build_daily_digest

router = APIRouter(prefix="/api/v1/daily-digest", tags=["Daily Digest"])
logger = logging.getLogger(__name__)


@router.get("", response_model=DailyDigest)
def get_daily_digest(
    date: str | None = Query(default=None, description="YYYY-MM-DD (생략 시 오늘)"),
    db: Session = Depends(get_db),
) -> DailyDigest:
    """ref_date 하루치 다이제스트를 반환한다."""
    if date is None:
        ref_date = dt.date.today()
    else:
        try:
            ref_date = dt.date.fromisoformat(date)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"invalid date: {date}") from exc
    return build_daily_digest(db, get_settings(), ref_date)
