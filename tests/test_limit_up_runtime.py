"""Runtime contract tests for the KIS upper-limit V1 orchestrator."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import threading

import pytest

KST = dt.timezone(dt.timedelta(hours=9))

from maps.common.models import SecurityMetadata
from maps.limit_up.runtime import (
    DeadmanMonitor,
    engine_active_at,
    eod_stage,
    is_v1_eligible_security,
    subscription_payload,
)


def test_subscription_payload_uses_official_realtime_tr_ids() -> None:
    """Each watched ticker needs both execution and best-book streams."""
    trade = json.loads(subscription_payload("key", "H0STCNT0", "005930"))
    quote = json.loads(subscription_payload("key", "H0STASP0", "005930"))

    assert trade["header"]["approval_key"] == "key"
    assert trade["body"]["input"]["tr_id"] == "H0STCNT0"
    assert quote["body"]["input"]["tr_id"] == "H0STASP0"
    assert quote["body"]["input"]["tr_key"] == "005930"


def test_security_eligibility_fails_closed_for_missing_new_or_preferred(db) -> None:
    """V1 scanner admits only seasoned KOSPI/KOSDAQ common stocks."""
    as_of = dt.date(2026, 8, 28)
    common = SecurityMetadata(
        ticker="005930",
        name="삼성전자",
        market="KOSPI",
        security_type="STOCK",
        listing_date=dt.date(1975, 6, 11),
    )
    preferred = SecurityMetadata(
        ticker="005935",
        name="삼성전자우",
        market="KOSPI",
        security_type="STOCK",
        listing_date=dt.date(1975, 6, 11),
    )
    new_stock = SecurityMetadata(
        ticker="123456",
        name="신규주",
        market="KOSDAQ",
        security_type="STOCK",
        listing_date=as_of - dt.timedelta(days=20),
    )

    assert is_v1_eligible_security(common, as_of=as_of) is True
    assert is_v1_eligible_security(preferred, as_of=as_of) is False
    assert is_v1_eligible_security(new_stock, as_of=as_of) is False
    assert is_v1_eligible_security(None, as_of=as_of) is False


def test_deadman_sends_success_fail_and_never_logs_secret_url() -> None:
    """Healthchecks receives state while the configured secret stays opaque."""
    sent: list[str] = []
    monitor = DeadmanMonitor("https://hc.example/secret", sender=sent.append)

    assert monitor.ping(healthy=True) is True
    assert monitor.ping(healthy=False) is True
    assert sent == ["https://hc.example/secret", "https://hc.example/secret/fail"]
    assert "secret" not in repr(monitor)


def test_empty_deadman_url_is_a_safe_noop() -> None:
    """Local development must not require an external monitor."""
    monitor = DeadmanMonitor("")

    assert monitor.ping(healthy=True) is False


def test_eod_stages_are_deadlines_not_windows() -> None:
    """Narrow windows made the absolute cap depend on process uptime.

    A restart during 15:18-15:20 used to skip the trim entirely, handing an
    unreviewed position to the next morning.
    """
    assert eod_stage(dt.time(15, 17, 59)) is None
    assert eod_stage(dt.time(15, 18)) == "cap"
    assert eod_stage(dt.time(15, 24, 59)) == "cap"
    assert eod_stage(dt.time(15, 25)) == "confirm"
    assert eod_stage(dt.time(15, 27, 59)) == "confirm"
    assert eod_stage(dt.time(15, 28)) == "force"

    # a process starting late must go straight to force, not replay a dead trim
    assert eod_stage(dt.time(15, 34)) == "force"
    assert eod_stage(dt.time(15, 30)) == "force"


def test_engine_is_idle_outside_trading_hours_and_days() -> None:
    """A 24/7 poll loop hammers the broker API; that is how accounts get locked.

    2026-07-27: pykrx re-login retries locked the KRX account 158 times in a day.
    """
    # 2026-08-29 is a Saturday
    assert not engine_active_at(dt.datetime(2026, 8, 29, 10, 0, tzinfo=KST))

    # weekday, but outside engine hours
    assert not engine_active_at(dt.datetime(2026, 8, 28, 8, 49, 59, tzinfo=KST))
    assert not engine_active_at(dt.datetime(2026, 8, 28, 15, 35, 1, tzinfo=KST))
    assert not engine_active_at(dt.datetime(2026, 8, 28, 22, 0, tzinfo=KST))


def test_engine_hours_cover_both_daily_action_windows() -> None:
    """08:59:30 next-open exits and the 15:18-15:28 overnight review must fit."""
    assert engine_active_at(dt.datetime(2026, 8, 28, 8, 59, 30, tzinfo=KST))
    assert engine_active_at(dt.datetime(2026, 8, 28, 15, 18, tzinfo=KST))
    assert engine_active_at(dt.datetime(2026, 8, 28, 15, 28, tzinfo=KST))


async def test_reconnect_clears_subscriptions_so_tickers_resubscribe() -> None:
    """Per-process subscription memory silently kills the feed after a reconnect.

    Worse than a plain outage: _feed_connected goes True, so the REST fallback
    stops too and the position is left with no price protection at all.
    """
    from maps.limit_up.runtime import KISIntradayRuntime

    runtime = object.__new__(KISIntradayRuntime)
    runtime._subscribed = {"005930"}
    sent: list[str] = []

    class _Socket:
        async def send(self, payload: str) -> None:
            sent.append(payload)

    socket = _Socket()

    # already-subscribed ticker is skipped while the connection lives
    await runtime._subscribe(socket, "key", "005930")
    assert sent == []

    # a new connection must forget what the old one had subscribed
    runtime._subscribed.clear()
    await runtime._subscribe(socket, "key", "005930")

    assert len(sent) == 2  # trade + quote streams
    assert "005930" in runtime._subscribed


def test_websocket_loop_actually_clears_subscriptions_on_connect() -> None:
    """The resubscribe fix is only real if the connect path clears the set.

    Same guard style as the liquidity cap's shared-implementation test: the
    behaviour above can pass while the loop never calls clear().
    """
    import inspect

    from maps.limit_up.runtime import KISIntradayRuntime

    source = inspect.getsource(KISIntradayRuntime._websocket_loop)
    assert "self._subscribed.clear()" in source
    # and it must happen before the socket is served, not after
    assert source.index("self._subscribed.clear()") < source.index("_serve_socket")


def _pump_runtime():
    """Build a runtime with only the pump's own state initialised."""
    import asyncio as _asyncio
    import itertools as _itertools

    from maps.limit_up.runtime import KISIntradayRuntime

    runtime = object.__new__(KISIntradayRuntime)
    runtime._stop = _asyncio.Event()
    runtime._service_queue = _asyncio.PriorityQueue()
    runtime._service_sequence = _itertools.count()
    return runtime


async def test_protective_work_runs_before_a_slow_entry_that_queued_first() -> None:
    """A slow buy must not delay a sell — that is the whole point of the ordering."""
    from maps.limit_up.runtime import _PRIORITY_HIGH, _PRIORITY_NORMAL

    runtime = _pump_runtime()
    order: list[str] = []
    started = asyncio.Event()
    release = threading.Event()

    def _slow_entry() -> str:
        order.append("entry")
        started.set()
        release.wait(timeout=5)
        return "entry"

    def _protective() -> str:
        order.append("protective")
        return "protective"

    pump = asyncio.create_task(runtime._service_pump())
    entry = asyncio.create_task(
        runtime._call_service(_slow_entry, priority=_PRIORITY_NORMAL)
    )
    await asyncio.wait_for(started.wait(), timeout=5)

    # queued while the entry is still in flight
    queued_first = asyncio.create_task(
        runtime._call_service(lambda: order.append("second_entry"),
                              priority=_PRIORITY_NORMAL)
    )
    await asyncio.sleep(0)
    protective = asyncio.create_task(
        runtime._call_service(_protective, priority=_PRIORITY_HIGH)
    )
    await asyncio.sleep(0)
    release.set()

    assert await asyncio.wait_for(entry, timeout=5) == "entry"
    assert await asyncio.wait_for(protective, timeout=5) == "protective"
    await asyncio.wait_for(queued_first, timeout=5)
    runtime._stop.set()
    pump.cancel()

    assert order == ["entry", "protective", "second_entry"]


async def test_a_blocking_service_call_never_freezes_the_event_loop() -> None:
    """Inline broker calls froze WebSocket reads; the loop must stay responsive."""
    runtime = _pump_runtime()
    release = threading.Event()
    ticks = 0

    def _blocking() -> str:
        release.wait(timeout=5)
        return "done"

    pump = asyncio.create_task(runtime._service_pump())
    call = asyncio.create_task(runtime._call_service(_blocking))
    for _ in range(5):
        await asyncio.sleep(0.01)
        ticks += 1  # the loop keeps running while the call blocks a worker thread

    release.set()
    assert await asyncio.wait_for(call, timeout=5) == "done"
    runtime._stop.set()
    pump.cancel()

    assert ticks == 5


async def test_service_errors_reach_the_caller_instead_of_killing_the_pump() -> None:
    """A swallowed exception would leave later calls hanging forever."""
    runtime = _pump_runtime()

    def _boom() -> None:
        raise ValueError("broker exploded")

    pump = asyncio.create_task(runtime._service_pump())
    with pytest.raises(ValueError, match="broker exploded"):
        await asyncio.wait_for(runtime._call_service(_boom), timeout=5)

    # the pump survives and still serves the next call
    assert await asyncio.wait_for(runtime._call_service(lambda: 42), timeout=5) == 42
    runtime._stop.set()
    pump.cancel()


def test_silent_feed_is_treated_as_dead() -> None:
    """A half-open socket keeps _feed_connected true, which suppresses REST fallback.

    Price protection then stops while the connection still looks healthy.
    """
    from maps.limit_up.runtime import _FEED_SILENCE_TIMEOUT_SECONDS, feed_is_silent

    assert not feed_is_silent(last_frame_at=100.0, now=100.0)
    assert not feed_is_silent(
        last_frame_at=100.0, now=100.0 + _FEED_SILENCE_TIMEOUT_SECONDS - 0.1
    )
    assert feed_is_silent(
        last_frame_at=100.0, now=100.0 + _FEED_SILENCE_TIMEOUT_SECONDS
    )


async def test_reading_the_next_frame_does_not_wait_for_the_previous_one() -> None:
    """A slow buy used to hold back every stop-loss tick queued behind it.

    The read loop must hand work to the queue and come straight back, since the
    queue already preserves order.
    """
    runtime = _pump_runtime()
    runtime.monotonic = lambda: 1.0
    runtime.wall_now = lambda: dt.datetime(2026, 8, 28, 10, 0, tzinfo=KST)
    runtime._quotes = {}
    runtime._feed_tasks = set()
    applied: list[str] = []
    release = threading.Event()

    def _slow_apply(event, now) -> None:
        applied.append("first")
        release.wait(timeout=5)

    runtime._apply_feed_event = _slow_apply
    pump = asyncio.create_task(runtime._service_pump())

    import maps.limit_up.runtime as runtime_module

    original = runtime_module.parse_kis_ws_message
    runtime_module.parse_kis_ws_message = lambda raw, received_at: [object()]
    try:
        # the dispatch returns immediately even though the work is still running
        assert await asyncio.wait_for(runtime.dispatch_message_async("x"), timeout=1) == 1
        assert await asyncio.wait_for(runtime.dispatch_message_async("y"), timeout=1) == 1
    finally:
        runtime_module.parse_kis_ws_message = original

    # let the fire-and-forget tasks reach the queue
    for _ in range(10):
        await asyncio.sleep(0.01)
    assert applied == ["first"]  # second is queued behind the slow one, in order

    release.set()
    await asyncio.wait_for(runtime._service_queue.join(), timeout=5)
    runtime._stop.set()
    pump.cancel()

    assert len(applied) == 2
