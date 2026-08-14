"""개인 설정 해석과 개인별 AI 분석 한도.

해석 순서는 **사용자 값 → 없으면 `UserPreferences` 스키마 기본값** 이다. 전역 `.env`
값으로 채우지 않는다 — `candidate_min_score` 를 전역 `MAPS_CANDIDATE_MIN_SCORE` 로
채우면 설정한 적 없는 사용자에게도 화면 필터가 걸리고, **화면 필터와 주문 게이트가
한 값으로 묶인다.** 미설정은 "전역값 적용"이 아니라 **"필터 없음"** 이다.

사용자 설정은 자기 화면에만 적용되며 운영 전역값을 덮어쓰지 못한다.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import func
from sqlalchemy.orm import Session

from maps.api.schemas import UserPreferences
from maps.common.models import AppUser, StockAnalysisHistory
from maps.common.settings import MapsSettings

_KST_OFFSET = dt.timedelta(hours=9)

# 계정별 한도가 없을 때 쓰는 기본값. 관리자는 한도를 적용하지 않는다.
DEFAULT_DAILY_ANALYSIS_LIMIT = 10


def resolve(user: AppUser | None, settings: MapsSettings | None = None) -> UserPreferences:
    """저장된 개인 설정을 검증해 반환한다. 없거나 깨졌으면 스키마 기본값.

    저장값에 모르는 키가 섞여 있어도 화면이 죽지 않도록, 검증 실패 시 기본값으로
    되돌린다(설정은 fail-safe 해도 되는 영역이다 — 주문 게이트가 아니다).

    **저장값에 남은 모르는 키는 버리고 검증한다.** `UserPreferences` 는 `extra=forbid`
    라서, 키를 하나 지우면 그 전에 저장한 계정의 JSON 이 통째로 검증에 실패한다.
    여기서 걸러 주지 않으면 `landing_screen` 까지 조용히 초기화된다 — 마이그레이션 없이
    배포되는 경로라 실제 설정이 사라진다(알림 3키 삭제 때 실제로 발생).
    **읽기만 관대하다.** `PUT /users/me/preferences` 는 계속 `extra=forbid` 로 422 를
    낸다 — 클라이언트가 오타 낸 키가 조용히 무시되면 안 된다.

    `settings` 는 결과에 영향을 주지 않는다. 전역 폴백을 없앤 뒤 호출부(`api/users.py`)
    호환을 위해 시그니처만 남긴 파라미터다.
    """
    stored = (user.preferences if user is not None else None) or {}
    known = {k: v for k, v in stored.items() if k in UserPreferences.model_fields}
    try:
        prefs = UserPreferences.model_validate(known)
    except Exception:  # noqa: BLE001 — 값이 깨진 설정은 기본값으로 취급한다
        prefs = UserPreferences()
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
