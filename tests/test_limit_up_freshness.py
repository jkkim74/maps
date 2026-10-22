"""Delayed/reconnected market data must never revive an obsolete entry signal."""

import datetime as dt
import asyncio
from dataclasses import replace

import pytest

from maps.common.settings import MapsSettings
from maps.limit_up.domain import LimitUpState
from maps.limit_up.runtime import KISIntradayRuntime
from maps.limit_up.service import LimitUpMode
from tests.test_limit_up_service import KST, ServiceBroker, _candidate, _service, _trade


def runtime_for(db, *, clock):
    """Use the real service and database, with a deterministic receive clock."""
    now = dt.datetime(2026, 8, 28, 10, tzinfo=KST)
    service = _service(db, LimitUpMode.RECOMMEND_ONLY)
    service.watch_candidate(_candidate(), now_kst=now)
    runtime = KISIntradayRuntime(settings=MapsSettings(), db=db, adapter=ServiceBroker(),
                                service=service, monotonic=lambda: clock[0],
                                wall_now=lambda: now + dt.timedelta(seconds=clock[0]))
    return runtime, service, now


@pytest.mark.parametrize("age", [3.0, 120.0])
def test_queued_old_cross_cannot_create_a_recommendation(db, age):
    """The worker must measure age when it runs, not trust captured callback time."""
    clock = [age + 2]
    runtime, service, now = runtime_for(db, clock=clock)
    runtime._apply_feed_event(_trade(1, 99_600), now)
    runtime._apply_feed_event(_trade(2, 99_800), now)
    assert service.machine("005930").state is LimitUpState.WATCHING
    assert service._sessions["005930"].trigger_at is None
    assert service.machine("005930").last_trade_price is None


def test_reconnect_reseeds_cross_and_does_not_keep_feed_halt(db):
    """A new connection requires a new below-to-above cross, without permanent latch."""
    clock = [1.0]
    runtime, service, now = runtime_for(db, clock=clock)
    service.on_trade(_trade(1, 99_600), now_kst=now)
    service.on_feed_disconnect(at=2, now_kst=now)
    service.on_feed_reconnect()
    service.on_trade(_trade(3, 99_800), now_kst=now)
    assert service.machine("005930").state is LimitUpState.WATCHING
    service.on_trade(_trade(4, 99_600), now_kst=now)
    service.on_trade(_trade(5, 99_800), now_kst=now)
    assert service.machine("005930").state is LimitUpState.NET_OPEN


def test_feed_recovery_never_clears_market_halt(db):
    """Recovering transport cannot clear the independent market-loss guard."""
    clock = [1.0]
    runtime, service, now = runtime_for(db, clock=clock)
    service._ensure_guard_for(now.date())
    service.machine("005930").on_market_halt(at=1)
    service.guard.halted_reasons.add("kosdaq_drawdown")
    service.on_feed_disconnect(at=2, now_kst=now)
    service.on_feed_reconnect()
    service.on_trade(_trade(3, 99_600), now_kst=now)
    service.on_trade(_trade(4, 99_800), now_kst=now)
    assert service.machine("005930").state is LimitUpState.WATCHING
    assert "kosdaq_drawdown" in service.guard.halted_reasons


def test_entry_predicate_uses_live_arrivals_but_fixed_signal_deadline():
    """Fresh arrivals permit normal KIS waits, never extend the original 15s signal."""
    from maps.limit_up.freshness import EntryFeed
    from maps.limit_up.domain import LimitUpConfig

    clock = [1.0]
    feed = EntryFeed(lambda: clock[0])
    signal = _trade(1, 99_800)
    version, gap = feed.observe(signal)
    check = feed.entry_check(signal, version, upper_limit_price=100_000,
                             config=LimitUpConfig())
    assert check()
    for at in (3, 5, 7, 9, 11, 13, 15):
        clock[0] = at
        feed.observe(_trade(at, 99_800))
        assert check()
    clock[0] = 16
    feed.observe(_trade(16, 99_800))
    assert not check()


@pytest.mark.parametrize("change", ["stale", "gap", "reset", "price", "strength", "buy", "turnover"])
def test_entry_predicate_rejects_invalidated_or_no_longer_eligible_signal(change):
    """A late HTTP send cannot reuse old epoch or gate values after current data changes."""
    from maps.limit_up.freshness import EntryFeed
    from maps.limit_up.domain import LimitUpConfig

    clock = [1.0]
    feed = EntryFeed(lambda: clock[0])
    signal = _trade(1, 99_800)
    version, _ = feed.observe(signal)
    check = feed.entry_check(signal, version, upper_limit_price=100_000,
                             config=LimitUpConfig())
    if change == "stale":
        clock[0] = 4
    elif change == "gap":
        clock[0] = 4
        feed.observe(_trade(4, 99_800))
    elif change == "reset":
        feed.reset()
        feed.observe(_trade(1, 99_800))
    else:
        bad = {"price": {"price": 99_600}, "strength": {"execution_strength": 149},
               "buy": {"buy_initiated": False}, "turnover": {"cumulative_turnover_krw": 49_999_999_999}}
        feed.observe(replace(signal, **bad[change]))
    assert not check()


def test_new_ticker_does_not_invalidate_another_tickers_signal():
    """An illiquid watched symbol must not stop unrelated healthy symbols."""
    from maps.limit_up.freshness import EntryFeed
    from maps.limit_up.domain import LimitUpConfig

    clock = [1.0]
    feed = EntryFeed(lambda: clock[0])
    signal = _trade(1, 99_800)
    version, _ = feed.observe(signal)
    check = feed.entry_check(signal, version, upper_limit_price=100_000,
                             config=LimitUpConfig())
    feed.observe(replace(signal, ticker="000660"))
    feed.reset(ticker="000660")
    assert check()


def test_shared_feed_ignores_trade_for_unwatched_upper_limit_symbol(db):
    """Shared Fujimoto subscriptions may include symbols without a V1 machine."""
    clock = [1.0]
    runtime, service, now = runtime_for(db, clock=clock)
    runtime._apply_feed_event(replace(_trade(1, 99_800), ticker="000660"), now)
    assert service.watched_tickers() == ("005930",)


def test_obsolete_callback_cannot_reset_a_recovered_new_cross(db):
    """An old connection callback may finish after the new generation was applied."""
    clock = [1.0]
    runtime, service, now = runtime_for(db, clock=clock)
    old = _trade(1, 99_600)
    version, _ = runtime._entry_feed.observe(old)
    runtime._entry_feed.reset()
    clock[0] = 2
    runtime._apply_feed_event(_trade(2, 99_600), now)
    clock[0] = 2.1
    runtime._apply_feed_event(_trade(2.1, 99_800), now)
    assert service.machine("005930").state is LimitUpState.NET_OPEN
    runtime._apply_feed_event(old, now, feed_version=version)
    assert service.machine("005930").state is LimitUpState.NET_OPEN


async def test_socket_ingest_is_bounded_and_invalidates_overflowed_decisions(db, monkeypatch):
    """Blocked broker work cannot accumulate an unbounded number of feed futures."""
    clock = [1.0]
    runtime, service, now = runtime_for(db, clock=clock)
    monkeypatch.setattr("maps.limit_up.runtime.parse_kis_ws_message",
                        lambda *args, **kwargs: [_trade(clock[0], 99_800)])
    try:
        for _ in range(300):
            await runtime.dispatch_message_async("trade")
        assert len(runtime._feed_tasks) <= 256
        assert runtime._feed_overflow_drops == 45
    finally:
        for task in runtime._feed_tasks:
            task.cancel()
        await asyncio.gather(*runtime._feed_tasks, return_exceptions=True)


def test_runtime_passes_original_deadline_and_live_predicate_to_service(db, monkeypatch):
    """Ingestion, not a later queued callback, fixes the signal lifetime."""
    clock = [1.0]
    runtime, service, now = runtime_for(db, clock=clock)
    captured = []
    monkeypatch.setattr(service, "on_trade", lambda event, **kwargs: captured.append(kwargs))
    runtime._apply_feed_event(_trade(1, 99_800), now)
    context = captured[0].get("entry_context")
    assert context is not None
    assert context.valid_until == now + dt.timedelta(seconds=15)
    assert context.quote_is_current()
    clock[0] = 4
    assert not context.quote_is_current()


async def test_index_backlog_is_bounded_without_losing_drawdown_evidence(db):
    """A blocked pump retains the observed peak-to-trough halt in constant space."""
    from maps.limit_up.feed import FeedIndex
    clock = [1.0]
    runtime, service, now = runtime_for(db, clock=clock)
    for i in range(300):
        value = 1_000 if i < 150 else 950
        runtime._on_ws_index(FeedIndex("1001", value, float(i + 1)), now)
    try:
        assert len(runtime._feed_tasks) <= 1
        async def immediate(fn, *args, **kwargs):
            kwargs.pop("priority", None)
            return fn(*args, **kwargs)
        runtime._call_service = immediate
        await asyncio.gather(*runtime._feed_tasks)
        assert "kosdaq_drawdown" in service.guard.halted_reasons
        assert service.guard.kosdaq_high == 1_000
    finally:
        for task in runtime._feed_tasks:
            task.cancel()
        await asyncio.gather(*runtime._feed_tasks, return_exceptions=True)


def test_silent_quote_stream_cannot_confirm_lock_and_preserves_time_stop(db):
    """A live socket/trade stream is insufficient proof of ten continuous lock seconds."""
    from maps.limit_up.feed import FeedQuote
    clock = [1.0]
    runtime, service, now = runtime_for(db, clock=clock)
    for at, price in ((1, 99_600), (2, 99_700), (3, 98_800)):
        clock[0] = at
        runtime._apply_feed_event(_trade(at, price), now)
    quote = FeedQuote("005930", 100_000, 0, 100_000, 100_000, 4)
    clock[0] = 4
    runtime._apply_feed_event(quote, now)
    assert service.machine("005930").lock_started_at == 4
    clock[0] = 14
    runtime._tick_service()
    assert service.machine("005930").state is LimitUpState.FILLED_WAIT_LOCK
    assert service.machine("005930").lock_started_at is None
    clock[0] = 183
    runtime._tick_service()
    assert service.machine("005930").state is LimitUpState.CLOSED
    assert service._sessions["005930"].end_reason == "time_stop"


def test_receipt_rechecked_after_slow_feed_gap_cleanup(db, monkeypatch):
    """Cancel/reconcile inside gap cleanup may age a frame before it reaches the FSM."""
    clock = [1.0]
    runtime, service, now = runtime_for(db, clock=clock)
    original = service.on_feed_gap
    def delayed(ticker, **kwargs):
        original(ticker, **kwargs)
        clock[0] = 5
    monkeypatch.setattr(service, "on_feed_gap", delayed)
    runtime._apply_feed_event(_trade(1, 99_600), now)
    assert service.machine("005930").last_trade_price is None


def test_entry_predicate_rejects_silent_quote_stream():
    """Fresh trades cannot conceal a gap in previously observed quote continuity."""
    from maps.limit_up.freshness import EntryFeed
    from maps.limit_up.feed import FeedQuote
    from maps.limit_up.domain import LimitUpConfig
    clock = [1.0]
    feed = EntryFeed(lambda: clock[0])
    feed.observe(FeedQuote("005930", 100_000, 0, 99_800, 1, 1))
    signal = _trade(1, 99_800)
    version, _ = feed.observe(signal)
    check = feed.entry_check(signal, version, upper_limit_price=100_000, config=LimitUpConfig())
    for at in (2, 3, 4):
        clock[0] = at
        feed.observe(_trade(at, 99_800))
    assert not check()


def test_second_leg_signal_invalidation_withdraws_first_leg(db):
    """Known-unsent leg B must not leave leg S live after its signal is invalid."""
    from maps.execution.safety import ExecutionContext
    from maps.limit_up.domain import build_grid
    from tests.test_limit_up_worker import ScriptedBroker, _worker

    broker = ScriptedBroker()
    worker, session = _worker(db, broker)
    context = ExecutionContext("", quote_is_current=lambda: len(broker.orders) == 0)
    result = worker.fire_grid(session, build_grid(upper_limit_price=100_000, budget_krw=2_000_000),
                              entry_context=context)
    assert len(broker.orders) == 1
    assert broker.cancelled == ["1001"]
    assert not result.open_buy_order_ids


async def test_scanner_filters_watched_and_ineligible_before_price_requests(db, monkeypatch):
    """Unnecessary per-stock REST calls must not consume the broker lane every scan."""
    from maps.common.models import SecurityMetadata

    clock = [1.0]
    runtime, service, now = runtime_for(db, clock=clock)
    db.add(SecurityMetadata(ticker="000660", name="Example", market="KOSPI", security_type="STOCK",
                            listing_date=dt.date(1980, 1, 1)))
    db.commit()
    details = []
    ranked = [{"ticker": t, "change_rate": 26.0} for t in ("005930", "000660", "999999")]

    class Adapter:
        def get_limit_up_rank_candidates(self):
            return ranked, 3

        def get_limit_up_candidate_details(self, ticker, *, ranked=None):
            details.append(ticker)
            return dict(ticker=ticker, market="KOSPI", upper_limit_price=100_000,
                        total_listed_shares=10_000_000, current_price=99_000, change_rate=26)

        def get_limit_up_candidates(self):
            return [self.get_limit_up_candidate_details(r["ticker"]) for r in ranked], 3

    async def direct(fn, *args, **kwargs):
        kwargs.pop("priority", None)
        return fn(*args, **kwargs)

    runtime.adapter = Adapter()
    monkeypatch.setattr(runtime, "_call_service", direct)
    assert await runtime.scan_once() == 1
    assert details == ["000660"]


async def test_slow_scan_does_not_block_protection_timers(db, monkeypatch):
    """The control loop must continue timers while one scan waits for broker data."""
    clock = [10.0]
    runtime, service, now = runtime_for(db, clock=clock)
    ticks = []
    release = asyncio.Event()
    async def slow_scan():
        await release.wait()
    async def no_work(*args, **kwargs):
        return None
    async def direct(fn, *args, **kwargs):
        kwargs.pop("priority", None)
        return fn(*args, **kwargs)
    def tick(**kwargs):
        ticks.append(1)
        if len(ticks) == 3:
            runtime._stop.set()
    monkeypatch.setattr(runtime, "scan_once", slow_scan)
    monkeypatch.setattr(runtime, "_call_service", direct)
    monkeypatch.setattr(service, "tick", tick)
    monkeypatch.setattr(runtime, "fallback_once", no_work)
    monkeypatch.setattr(runtime, "_run_daily_actions", no_work)
    monkeypatch.setattr("maps.limit_up.runtime.index_guard_active_at", lambda wall: False)
    try:
        await asyncio.wait_for(runtime._control_loop(), timeout=2)
        assert len(ticks) == 3
    finally:
        release.set()
        if runtime._scan_task is not None:
            await runtime._scan_task


async def test_status_exposes_refused_automatic_reason(monkeypatch):
    """API liveness must not disguise an engine that refused its configured mode."""
    from maps.limit_up import bootstrap
    from maps.api.limit_up import get_limit_up_status

    settings = MapsSettings(maps_broker_mode="kis", maps_limit_up_enabled=True,
        maps_limit_up_mode="automatic", maps_live_trading_enabled=False)
    monkeypatch.setattr(bootstrap, "_runtime", None)
    monkeypatch.setattr("maps.api.limit_up.get_settings", lambda: settings)
    assert await bootstrap.start_limit_up_if_enabled(settings) is bootstrap.StartOutcome.REFUSED
    result = get_limit_up_status().model_dump()
    assert result.get("configured_mode") == "automatic"
    assert result.get("start_outcome") == "refused"
    assert result.get("start_reason") == "live_trading_disabled"
    assert result["mode"] == "off"


def test_status_exposes_feed_backlog_and_drops(db, monkeypatch):
    from maps.limit_up import bootstrap
    from maps.api.limit_up import get_limit_up_status

    runtime, service, now = runtime_for(db, clock=[1.0])
    runtime._feed_stale_drops = 2
    runtime._feed_overflow_drops = 3
    monkeypatch.setattr(bootstrap, "_runtime", runtime)
    result = get_limit_up_status().model_dump()
    assert result.get("feed_state") == "disconnected"
    assert result.get("feed_stale_drops") == 2
    assert result.get("feed_overflow_drops") == 3


def test_shared_fujimoto_runtime_does_not_report_upper_limit_running(db, monkeypatch):
    """A shared live socket does not imply V1 scanning/decisions are enabled."""
    from maps.limit_up import bootstrap
    from maps.api.limit_up import get_limit_up_status
    runtime, service, now = runtime_for(db, clock=[1.0])
    runtime.upper_enabled = False
    monkeypatch.setattr(bootstrap, "_runtime", runtime)
    result = get_limit_up_status()
    assert result.running is False
    assert result.mode == "off"
    assert result.entry_halted is True
