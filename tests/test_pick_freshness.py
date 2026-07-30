"""분석 워치리스트 픽 신선도 판정 테스트.

날짜 연산 자체는 `today` 주입으로 고정해 검증한다(주말·휴장일을 실제로 넘는지 확인).
`datetime.date.today` 를 전역 monkeypatch 하지 않는다 — SQLAlchemy 컬럼 기본값 등
무관한 코드가 같이 흔들린다.
"""

from __future__ import annotations

import datetime as dt

from maps.common.models import AnalysisPick
from maps.common.settings import MapsSettings
from maps.ops.pick_freshness import (
    STALE_REASON_EXPIRED,
    is_pick_stale,
    pick_age_trading_days,
    pick_cutoff_date,
    pick_stale_reason,
)

# 2026-07-30 은 목요일. 5거래일 전은 7/23(목) — 주말 하나를 건넌다.
_TODAY = dt.date(2026, 7, 30)


def _settings(max_age: int = 5) -> MapsSettings:
    return MapsSettings(maps_analysis_pick_max_age_trading_days=max_age)


def _pick(ref_date: dt.date) -> AnalysisPick:
    return AnalysisPick(ref_date=ref_date, ticker="005930", name="삼성전자", source="manual")


def test_cutoff_counts_trading_days_not_calendar_days() -> None:
    # 달력일이면 7/25, 거래일이면 주말을 건너 7/23
    assert pick_cutoff_date(_settings(5), today=_TODAY) == dt.date(2026, 7, 23)


def test_cutoff_zero_max_age_is_today() -> None:
    assert pick_cutoff_date(_settings(0), today=_TODAY) == _TODAY


def test_boundary_ref_date_is_still_fresh() -> None:
    """`ref_date == cutoff` 는 아직 신선하다(경계 포함)."""
    cutoff = pick_cutoff_date(_settings(5), today=_TODAY)
    assert is_pick_stale(_pick(cutoff), cutoff) is False
    assert is_pick_stale(_pick(cutoff - dt.timedelta(days=1)), cutoff) is True


def test_stale_reason_is_expired_or_none() -> None:
    cutoff = pick_cutoff_date(_settings(5), today=_TODAY)
    assert pick_stale_reason(_pick(dt.date(2026, 6, 30)), cutoff) == STALE_REASON_EXPIRED
    assert pick_stale_reason(_pick(_TODAY), cutoff) is None


def test_missing_ref_date_is_not_stale() -> None:
    """판정 불가를 만료로 취급하면 데이터 결손이 곧 차단이 된다 — 신선으로 둔다."""
    cutoff = pick_cutoff_date(_settings(5), today=_TODAY)
    assert is_pick_stale(_pick(None), cutoff) is False  # type: ignore[arg-type]


def test_age_counts_trading_days() -> None:
    s = _settings(5)
    assert pick_age_trading_days(_pick(_TODAY), settings=s, today=_TODAY) == 0
    # 7/29(수) → 1거래일, 7/27(월) → 3거래일, 7/24(금) → 4거래일
    assert pick_age_trading_days(_pick(dt.date(2026, 7, 29)), settings=s, today=_TODAY) == 1
    assert pick_age_trading_days(_pick(dt.date(2026, 7, 27)), settings=s, today=_TODAY) == 3
    assert pick_age_trading_days(_pick(dt.date(2026, 7, 24)), settings=s, today=_TODAY) == 4


def test_age_is_capped_not_unbounded() -> None:
    """아주 오래된 픽은 상한값을 반환한다 — 정확한 숫자보다 비용 상한이 중요하다."""
    age = pick_age_trading_days(_pick(dt.date(2020, 1, 2)), settings=_settings(5), today=_TODAY)
    assert age == 60


def test_future_ref_date_is_not_stale() -> None:
    s = _settings(5)
    future = _TODAY + dt.timedelta(days=3)
    assert pick_age_trading_days(_pick(future), settings=s, today=_TODAY) == 0
    assert is_pick_stale(_pick(future), pick_cutoff_date(s, today=_TODAY)) is False
