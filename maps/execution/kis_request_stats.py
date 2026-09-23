"""KIS REST 요청 결과를 **시도 단위**로 센다 — 한도 초과·무응답을 운영 로그로 보이게 한다.

재시도 중간 실패는 어댑터에서 DEBUG 로만 남아, 하루에 한도 초과(EGW00201)나 read timeout 이
몇 번 났는지 운영에서 셀 수 없었다(재시도 소진분만 WARNING). 그래서 모의 계좌의 실제
호출 한도가 몇 건/초인지도 판별할 수 없었다(2026-09-23 검토).

- 1분 창마다 INFO 요약 한 줄: ``KIS req summary 60s: n=… ok=… rate_limited=… …``
- KST 일자별 누적: 장마감 리포트가 읽는다. 프로세스 메모리라 재시작하면 0 부터 다시 센다
  (``since`` 로 기동 시각을 함께 준다).
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
import time
from collections import Counter
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

_KST = dt.timezone(dt.timedelta(hours=9))
_SUMMARY_WINDOW_SECONDS = 60.0

#: 결과 분류. ``rate_limited`` 는 게이트웨이 한도 거절(EGW00201/EGW00215).
OUTCOMES = ("ok", "rate_limited", "http_error", "read_timeout", "connect_timeout", "exception")


@dataclass
class KisDayTotals:
    """KST 하루치 시도 결과 누적."""

    day: dt.date
    since: dt.datetime
    counts: Counter = field(default_factory=Counter)

    @property
    def requests(self) -> int:
        """전체 시도 수."""
        return sum(self.counts.values())


class KisRequestStats:
    """스레드 안전한 KIS 요청 결과 집계기."""

    def __init__(self, *, clock=time.monotonic, now=lambda: dt.datetime.now(_KST)) -> None:  # noqa: ANN001
        self._lock = threading.Lock()
        self._clock = clock
        self._now = now
        self._window_started = clock()
        self._window: Counter = Counter()
        self._latencies: list[float] = []
        self._day: KisDayTotals | None = None

    def record(self, outcome: str, *, latency_ms: float) -> None:
        """시도 하나의 결과를 더하고, 창이 찼으면 요약 한 줄을 남긴다."""
        summary: str | None = None
        with self._lock:
            now = self._now()
            if self._day is None or self._day.day != now.date():
                self._day = KisDayTotals(day=now.date(), since=now)
            self._day.counts[outcome] += 1
            self._window[outcome] += 1
            self._latencies.append(latency_ms)
            if self._clock() - self._window_started >= _SUMMARY_WINDOW_SECONDS:
                summary = self._format_window()
                self._window = Counter()
                self._latencies = []
                self._window_started = self._clock()
        if summary is not None:
            logger.info(summary)

    def totals(self, day: dt.date) -> KisDayTotals | None:
        """``day``(KST) 누적. 이 프로세스가 그날 요청을 한 번도 안 했으면 None."""
        with self._lock:
            if self._day is None or self._day.day != day:
                return None
            return KisDayTotals(day=self._day.day, since=self._day.since, counts=Counter(self._day.counts))

    def _format_window(self) -> str:
        latencies = sorted(self._latencies)
        p95 = latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))] if latencies else 0.0
        parts = " ".join(f"{name}={self._window.get(name, 0)}" for name in OUTCOMES)
        return (
            f"KIS req summary {_SUMMARY_WINDOW_SECONDS:.0f}s: n={sum(self._window.values())} "
            f"{parts} p95={p95:.0f}ms max={latencies[-1] if latencies else 0:.0f}ms"
        )


#: 프로세스 전역 집계기 — 어댑터 인스턴스가 여러 개여도 한 곳에 모은다.
KIS_REQUEST_STATS = KisRequestStats()
