"""KIS 요청 시도 단위 집계 테스트."""

from __future__ import annotations

import datetime as dt

import pytest

from maps.execution.kis_request_stats import KisRequestStats

_KST = dt.timezone(dt.timedelta(hours=9))


def _stats(clock: list[float], now: list[dt.datetime]) -> KisRequestStats:
    return KisRequestStats(clock=lambda: clock[0], now=lambda: now[0])


def test_summary_line_after_window(caplog: pytest.LogCaptureFixture) -> None:
    clock = [0.0]
    now = [dt.datetime(2026, 9, 24, 9, 0, tzinfo=_KST)]
    stats = _stats(clock, now)

    with caplog.at_level("INFO", logger="maps.execution.kis_request_stats"):
        stats.record("ok", latency_ms=100)
        stats.record("rate_limited", latency_ms=50)
        assert not caplog.records
        clock[0] = 61.0
        stats.record("read_timeout", latency_ms=8000)

    [line] = [r.getMessage() for r in caplog.records]
    assert "n=3" in line and "ok=1" in line and "rate_limited=1" in line and "read_timeout=1" in line


def test_day_totals_reset_on_kst_date_change() -> None:
    clock = [0.0]
    now = [dt.datetime(2026, 9, 24, 15, 0, tzinfo=_KST)]
    stats = _stats(clock, now)
    stats.record("rate_limited", latency_ms=10)
    stats.record("ok", latency_ms=10)

    totals = stats.totals(dt.date(2026, 9, 24))
    assert totals is not None
    assert totals.requests == 2 and totals.counts["rate_limited"] == 1

    now[0] = dt.datetime(2026, 9, 25, 9, 0, tzinfo=_KST)
    stats.record("ok", latency_ms=10)
    assert stats.totals(dt.date(2026, 9, 24)) is None
    assert stats.totals(dt.date(2026, 9, 25)).requests == 1
