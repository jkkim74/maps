"""개인 설정 해석과 개인별 AI 분석 한도.

해석 순서는 **사용자 값 → 없으면 전역 `get_settings()` 기본값** 이다. 사용자 설정은
자기 화면에만 적용되며 운영 전역값(`.env`)을 덮어쓰지 못한다.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import func
from sqlalchemy.orm import Session

from maps.api.schemas import UserPreferences
from maps.common.models import AppUser, StockAnalysisHistory
from maps.common.settings import MapsSettings, get_settings

_KST_OFFSET = dt.timedelta(hours=9)

# 계정별 한도가 없을 때 쓰는 기본값. 관리자는 한도를 적용하지 않는다.
DEFAULT_DAILY_ANALYSIS_LIMIT = 10


def resolve(user: AppUser | None, settings: MapsSettings | None = None) -> UserPreferences:
    """저장된 개인 설정을 검증해 반환한다. 없거나 깨졌으면 전역 기본값.

    저장값에 모르는 키가 섞여 있어도 화면이 죽지 않도록, 검증 실패 시 기본값으로
    되돌린다(설정은 fail-safe 해도 되는 영역이다 — 주문 게이트가 아니다).
    """
    settings = settings or get_settings()
    stored = (user.preferences if user is not None else None) or {}
    try:
        prefs = UserPreferences.model_validate(stored)
    except Exception:  # noqa: BLE001 — 손상된 설정은 기본값으로 취급한다
        prefs = UserPreferences()
    if prefs.candidate_min_score is None:
        prefs.candidate_min_score = settings.maps_candidate_min_score
    return prefs


def kst_day_start_utc_naive(today: dt.date | None = None) -> dt.datetime:
    """오늘(KST) 자정을 DB의 UTC-naive `created_at` 경계로 변환한다."""
    now_kst = dt.datetime.now(dt.timezone.utc) + _KST_OFFSET
    day = today or now_kst.date()
    return dt.datetime.combine(day, dt.time.min) - _KST_OFFSET


def daily_analysis_limit(user: AppUser | None) -> int:
    """계정의 하루 분석 허용 횟수. 관리자는 무제한(-1)."""
    if user is None or user.role == "admin":
        return -1
    if user.daily_analysis_limit is not None:
        return max(0, user.daily_analysis_limit)
    return DEFAULT_DAILY_ANALYSIS_LIMIT


def analysis_used_today(db: Session, user: AppUser | None) -> int:
    """오늘(KST) 이 계정이 실행한 종목분석 건수."""
    if user is None:
        return 0
    return (
        db.query(func.count(StockAnalysisHistory.id))
        .filter(StockAnalysisHistory.owner_user_id == user.id)
        .filter(StockAnalysisHistory.created_at >= kst_day_start_utc_naive())
        .scalar()
        or 0
    )


def analysis_quota_exceeded(db: Session, user: AppUser | None) -> bool:
    """한도를 이미 다 썼는지 판단한다. 초과면 **AI를 호출하기 전에** 막아야 한다."""
    limit = daily_analysis_limit(user)
    if limit < 0:
        return False
    return analysis_used_today(db, user) >= limit
