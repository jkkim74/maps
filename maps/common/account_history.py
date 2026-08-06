"""현재 브로커 계좌의 성과 이력 경계 계산."""

from __future__ import annotations

import datetime as dt

from maps.common.settings import MapsSettings, get_settings


_KST_OFFSET = dt.timedelta(hours=9)
_KST = dt.timezone(_KST_OFFSET)


def account_history_start_date(settings: MapsSettings | None = None) -> dt.date | None:
    """현재 계좌 이력을 시작할 KST 날짜를 반환한다."""
    configured = settings or get_settings()
    return configured.maps_account_history_start_date


def account_history_start_utc_naive(settings: MapsSettings | None = None) -> dt.datetime | None:
    """KST 기준일 자정을 DB의 UTC-naive `created_at` 경계로 변환한다."""
    start = account_history_start_date(settings)
    if start is None:
        return None
    return dt.datetime.combine(start, dt.time.min) - _KST_OFFSET


def clamp_history_start_date(
    requested: dt.date,
    settings: MapsSettings | None = None,
) -> dt.date:
    """조회 시작일을 현재 계좌 시작일보다 과거로 내려가지 않게 제한한다."""
    account_start = account_history_start_date(settings)
    return max(requested, account_start) if account_start is not None else requested


def utc_datetime_to_kst_date(value: dt.datetime) -> dt.date:
    """DB의 UTC datetime(naive/aware)을 KST 거래일로 변환한다."""
    if value.tzinfo is None:
        return (value + _KST_OFFSET).date()
    return value.astimezone(_KST).date()
