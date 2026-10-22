"""Process-local receipt evidence shared by socket ingestion and order submission."""

from __future__ import annotations

import threading
from collections.abc import Callable

from maps.limit_up.domain import LimitUpConfig, trigger_price
from maps.limit_up.feed import FeedIndex, FeedQuote, FeedTrade

QUOTE_MAX_AGE = 3.0
SIGNAL_MAX_AGE = 15.0
FeedVersion = tuple[int, int]


class IndexWindow:
    """Bound delayed index observations while preserving every possible halt.

    The minimum tests an earlier persisted high; the worst observed high/low pair
    tests this window's drawdown; the maximum carries its high into the next one.
    Replaying these actual observations in arrival order cannot invent a decline.
    """

    def __init__(self) -> None:
        self.minimum: FeedIndex | None = None
        self.peak: FeedIndex | None = None
        self.decline: tuple[FeedIndex, FeedIndex] | None = None
        self.drawdown = 0.0

    def observe(self, event: FeedIndex) -> None:
        """Retain at most four actual prints, regardless of ingestion backlog."""
        if self.minimum is None or event.value < self.minimum.value:
            self.minimum = event
        if self.peak is None or event.value > self.peak.value:
            self.peak = event
        drawdown = (self.peak.value - event.value) / self.peak.value
        if drawdown > self.drawdown:
            self.drawdown = drawdown
            self.decline = (self.peak, event)

    def samples(self) -> list[FeedIndex]:
        """Return the retained evidence in its original monotonic arrival order."""
        events = [self.minimum, self.peak, *(self.decline or ())]
        return sorted({event for event in events if event is not None},
                      key=lambda event: event.received_at)


class EntryFeed:
    """Keep current observations available while the serialized order lane waits."""

    def __init__(self, monotonic: Callable[[], float]) -> None:
        self._now = monotonic
        self._lock = threading.RLock()
        self._epoch = 0
        self._versions: dict[str, int] = {}
        self._trades: dict[str, FeedTrade] = {}
        self._arrivals: dict[tuple[str, type], float] = {}
        self.gaps = 0

    def reset(self, *, ticker: str | None = None) -> None:
        """Invalidate signals permanently; later arrivals cannot revive them."""
        with self._lock:
            self.gaps += 1
            if ticker is None:
                self._epoch += 1
                self._trades.clear()
                self._arrivals.clear()
            else:
                self._versions[ticker] = self._versions.get(ticker, 0) + 1
                self._trades.pop(ticker, None)
                for key in list(self._arrivals):
                    if key[0] == ticker:
                        del self._arrivals[key]

    def observe(self, event: FeedTrade | FeedQuote) -> tuple[FeedVersion, bool]:
        """Publish receipt before queuing decisions; identify per-stream continuity gaps."""
        with self._lock:
            key = (event.ticker, type(event))
            previous = self._arrivals.get(key)
            gap = previous is not None and not 0 <= event.received_at - previous < QUOTE_MAX_AGE
            if gap:
                self.reset(ticker=event.ticker)
            self._arrivals[key] = event.received_at
            if isinstance(event, FeedTrade):
                self._trades[event.ticker] = event
            return (self._epoch, self._versions.get(event.ticker, 0)), gap

    def current(self, ticker: str, version: FeedVersion) -> bool:
        """Check whether queued decision work still belongs to the current feed."""
        with self._lock:
            return version == (self._epoch, self._versions.get(ticker, 0))

    def fresh(self, ticker: str) -> bool:
        """Any fresh instrument stream can support existing position monitoring."""
        with self._lock:
            arrivals = [at for (symbol, _), at in self._arrivals.items() if symbol == ticker]
            return bool(arrivals and 0 <= self._now() - max(arrivals) < QUOTE_MAX_AGE)

    def continuous(self, ticker: str, version: FeedVersion | None) -> bool:
        """Every previously observed stream must remain fresh in this generation."""
        with self._lock:
            arrivals = [at for (symbol, _), at in self._arrivals.items() if symbol == ticker]
            return bool(version is not None and self.current(ticker, version) and arrivals
                        and all(0 <= self._now() - at < QUOTE_MAX_AGE for at in arrivals))

    def age(self, ticker: str) -> float | None:
        """Report observed receipt age, never substitute an observation timestamp."""
        with self._lock:
            arrivals = [at for (symbol, _), at in self._arrivals.items() if symbol == ticker]
            return self._now() - max(arrivals) if arrivals else None

    def entry_check(self, signal: FeedTrade, version: FeedVersion, *,
                    upper_limit_price: int, config: LimitUpConfig) -> Callable[[], bool]:
        """Capture fixed signal identity/deadline, re-evaluating actual current trade gates."""
        def is_current() -> bool:
            with self._lock:
                now = self._now()
                latest = self._trades.get(signal.ticker)
                return bool(
                    self.continuous(signal.ticker, version)
                    and 0 <= now - signal.received_at < SIGNAL_MAX_AGE
                    and latest is not None
                    and latest.received_at >= signal.received_at
                    and 0 <= now - latest.received_at < QUOTE_MAX_AGE
                    and trigger_price(upper_limit_price) <= latest.price < upper_limit_price
                    and latest.buy_initiated
                    and latest.cumulative_turnover_krw >= config.min_turnover_krw
                    and latest.execution_strength >= config.min_execution_strength
                )
        return is_current
