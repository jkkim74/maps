"""Calendar reuse must preserve the public KRX closure rules."""

from __future__ import annotations

import datetime as dt
import sys
from unittest.mock import patch

import holidays
import pytest

from maps.market import trading_rules


@pytest.fixture(autouse=True)
def clear_yearly_calendar_cache():
    """Keep cached calendars independent of other tests and their patches."""
    cached = getattr(trading_rules, "_korean_holiday_dates", None)
    if cached is not None:
        cached.cache_clear()
    yield
    if cached is not None:
        cached.cache_clear()


@pytest.mark.parametrize(
    ("target", "closed"),
    [
        (dt.date(2024, 1, 1), True),
        (dt.date(2024, 1, 2), False),
        (dt.date(2024, 2, 9), True),
        (dt.date(2025, 1, 2), False),
        (dt.date(2025, 1, 28), True),
        (dt.date(2025, 12, 31), True),
        (dt.date(2026, 1, 2), False),
        (dt.date(2026, 2, 17), True),
        (dt.date(2026, 5, 1), True),
        (dt.date(2026, 10, 24), True),
    ],
)
def test_representative_calendar_dates_are_unchanged(target: dt.date, closed: bool) -> None:
    assert trading_rules.is_krx_closed_date(target) is closed


def test_calendar_is_constructed_once_per_reused_year() -> None:
    with patch.object(holidays, "KR", wraps=holidays.KR) as construct:
        for _ in range(3):
            assert trading_rules.is_krx_closed_date(dt.date(2024, 2, 9))
            assert not trading_rules.is_krx_closed_date(dt.date(2024, 1, 2))
            assert trading_rules.is_krx_closed_date(dt.date(2025, 1, 28))
        assert construct.call_count == 2


def test_yearly_calendar_cache_is_bounded_and_immutable() -> None:
    cached = getattr(trading_rules, "_korean_holiday_dates", None)
    assert cached is not None, "yearly holiday dates must be cached"
    bound = cached.cache_info().maxsize
    assert bound is not None and 0 < bound <= 64
    with patch.object(holidays, "KR", wraps=holidays.KR) as construct:
        for year in range(2000, 2000 + bound + 1):
            assert isinstance(cached(year), frozenset)
        assert cached.cache_info().currsize == bound
        calls_before_evicted_year = construct.call_count
        assert trading_rules.is_krx_closed_date(dt.date(2000, 1, 3)) is False
        assert construct.call_count == calls_before_evicted_year + 1


def test_extra_closures_remain_dynamic_after_calendar_is_cached() -> None:
    target = dt.date(2026, 10, 21)
    closures: set[dt.date] = set()
    assert not trading_rules.is_krx_closed_date(target, extra_closed_dates=closures)
    closures.add(target)
    assert trading_rules.is_krx_closed_date(target, extra_closed_dates=closures)
    closures.clear()
    assert not trading_rules.is_krx_closed_date(target, extra_closed_dates=closures)


def test_missing_dependency_fallback_remains_after_warming_cache(monkeypatch, caplog) -> None:
    holiday = dt.date(2026, 2, 17)
    assert trading_rules.is_krx_closed_date(holiday)
    monkeypatch.setitem(sys.modules, "holidays", None)
    assert not trading_rules.is_krx_closed_date(holiday)
    assert "holidays package unavailable" in caplog.text
    assert trading_rules.is_krx_closed_date(dt.date(2026, 10, 24))
    assert trading_rules.is_krx_closed_date(dt.date(2026, 5, 1))
    assert trading_rules.is_krx_closed_date(holiday, extra_closed_dates={holiday})
