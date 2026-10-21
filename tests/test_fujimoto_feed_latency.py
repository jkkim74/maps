"""Offline concurrency regressions for actual quote recording and decision clocks."""
import asyncio
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import threading
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from maps.common.models import Base, FujimotoEvidence
from maps.common.settings import MapsSettings
from maps.execution.safety import account_execution_lock, account_key
from maps.fujimoto.domain import CycleState, Mode, RuleEvidence, evaluate
from maps.fujimoto.repository import FillEvent
from maps.fujimoto.service import FujimotoService
from maps.limit_up.feed import FeedQuote, KIS_ASK_COLUMNS
from maps.limit_up.runtime import KISIntradayRuntime


@pytest.fixture
def file_db(tmp_path):
    """Independent connections, unlike a shared in-memory SQLite connection."""
    engine = create_engine(f"sqlite:///{tmp_path / 'feed.sqlite'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db
    engine.dispose()


def held_lock(key):
    """Hold the real production account lock on another thread until released."""
    acquired, release = threading.Event(), threading.Event()
    def hold():
        with account_execution_lock(key):
            acquired.set()
            assert release.wait(10), "test failed to release account lock"
    thread = threading.Thread(target=hold)
    thread.start()
    assert acquired.wait(2)
    return release, thread


def frame(now):
    """An actual parser input, with unchanged receipt and exchange clocks."""
    values = {"MKSC_SHRN_ISCD": "005930", "BSOP_HOUR": now.astimezone(timezone(timedelta(hours=9))).strftime("%H%M%S"),
              "ASKP1": "1101", "BIDP1": "1100", "ASKP_RSQN1": "10", "BIDP_RSQN1": "30",
              "TOTAL_ASKP_RSQN": "10", "TOTAL_BIDP_RSQN": "30"}
    return "0|H0STASP0|001|" + "^".join(values.get(k, "0") for k in KIS_ASK_COLUMNS)


async def test_production_quote_records_while_execution_pump_waits_on_real_lock(file_db, monkeypatch):
    """Raw evidence commits while active work waits; no shared Session or orders."""
    settings = MapsSettings(_env_file=None)
    service = FujimotoService(file_db, Mock(), settings=settings)
    now = datetime.now(timezone.utc)
    runtime = KISIntradayRuntime(settings=settings, db=file_db, adapter=Mock(), service=Mock(),
                                fujimoto=service, upper_enabled=False, wall_now=lambda: now)
    async def idle():
        await asyncio.Event().wait()
    monkeypatch.setattr(runtime, "_control_loop", idle)
    monkeypatch.setattr(runtime, "_websocket_loop", idle)
    await runtime.start()
    release, thread = held_lock(account_key(settings))
    started = threading.Event()
    def reconcile():
        started.set()
        with account_execution_lock(account_key(settings)):
            pass
    pending = asyncio.create_task(runtime._call_service(reconcile))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        await asyncio.wait_for(runtime.dispatch_message_async(frame(now)), 2)
        # Recorder owns a separate session and never needs the execution worker.
        for _ in range(100):
            with Session(file_db.get_bind()) as reader:
                rows = reader.query(FujimotoEvidence).filter_by(kind="quote").all()
                if rows:
                    break
            await asyncio.sleep(.01)
        assert len(rows) == 1, "quote recording waited for account/order execution"
        assert rows[0].payload["received_at"] == now.isoformat()
        assert rows[0].payload["processed_at"] == now.isoformat()
        assert not pending.done()
        service.manager.submit.assert_not_called()
        service.manager.submit_exit.assert_not_called()
    finally:
        release.set()
        await pending
        thread.join(2)
        await runtime.stop()


async def test_observe_quote_and_timer_do_not_wait_for_account_lock(file_db):
    """Observation never needs an execution lock, even without a runtime recorder."""
    service = FujimotoService(file_db, Mock())
    now = datetime.now(timezone.utc)
    release, thread = held_lock(account_key(service.settings))
    def observe():
        service.tick(now=now)
        service.on_quote(FeedQuote("AAA", 1101, 10, 1100, 30, 1., 10, 30, now, now), now=now)
    pending = asyncio.create_task(asyncio.to_thread(observe))
    try:
        await asyncio.wait_for(asyncio.shield(pending), .5)
    finally:
        release.set()
        await pending
        thread.join(2)


async def test_quote_stale_after_actual_account_lock_wait_cannot_decide(file_db, monkeypatch):
    """Elapsed time changes decision freshness, never the captured receipt time."""
    import maps.fujimoto.service as module
    now = datetime(2026, 10, 8, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(module, "utcnow", lambda: now.replace(tzinfo=None))
    elapsed = [0.]
    monkeypatch.setattr(module, "monotonic", lambda: elapsed[0], raising=False)
    service = FujimotoService(file_db, Mock())
    repo, key = service.repo, account_key(service.settings)
    rule = RuleEvidence(date(2026, 10, 7), 1000, True, financial_status="maintained",
                        daily_rsi=40, weekly_rsi=50, macd_golden=True)
    source = repo.record_evidence("candidate", "AAA", now - timedelta(days=1), now - timedelta(days=1), {"rule": rule})
    config = repo.configure(key, 7, Mode.SAFE, 5000000)
    cycle = repo.create_cycle(config.id, "AAA")
    order = repo.reserve_order(cycle.id, evaluate(Mode.SAFE, rule, CycleState()), source.id,
                               10, 1000, signal_date=rule.as_of, stop_price=920)
    repo.apply_fill(FillEvent(order.id, key, None, 10, 10000, 0, 0, "FILLED", now.date()))
    repo.record_evidence("control", "*", now - timedelta(minutes=1), now - timedelta(minutes=1),
                        {"execution_mode": "paper", "entries_enabled": False, "sell_consent": True}, account_key=key)
    file_db.commit()
    monkeypatch.setattr("maps.fujimoto.sources.current_financial_status", lambda *a: "maintained")
    submitted = Mock()
    monkeypatch.setattr(service, "_submit_decision", submitted)
    attempted = threading.Event()
    @contextmanager
    def watched_lock(lock_key):
        attempted.set()
        with account_execution_lock(lock_key):
            yield
    monkeypatch.setattr(module, "account_execution_lock", watched_lock)
    release, thread = held_lock(key)
    quote = FeedQuote("AAA", 901, 10, 900, 30, 1., 10, 30, now, now)
    pending = asyncio.create_task(asyncio.to_thread(service.on_quote, quote, now=now))
    try:
        assert await asyncio.to_thread(attempted.wait, 2)
        elapsed[0] = 4.
    finally:
        release.set()
        await pending
        thread.join(2)
    submitted.assert_not_called()
    assert quote.received_utc == now


async def start_recorder(db, monkeypatch, **settings_kwargs):
    """Start real workers without network/control producers."""
    settings = MapsSettings(_env_file=None, **settings_kwargs)
    service = FujimotoService(db, Mock(), settings=settings)
    clock = [datetime.now(timezone.utc)]
    runtime = KISIntradayRuntime(settings=settings, db=db, adapter=Mock(), service=Mock(),
                                fujimoto=service, upper_enabled=False, wall_now=lambda: clock[0])
    async def idle():
        await asyncio.Event().wait()
    monkeypatch.setattr(runtime, "_control_loop", idle)
    monkeypatch.setattr(runtime, "_websocket_loop", idle)
    await runtime.start()
    return runtime, service, clock


async def test_recorder_fifo_reconnect_capacity_and_separate_session(file_db, monkeypatch):
    runtime, service, clock = await start_recorder(file_db, monkeypatch)
    runtime._recording_feed.capacity = 2
    assert runtime._recording_db is not service.db
    assert runtime._recording_queue.maxsize == 256
    try:
        await runtime._call_recording(runtime._recording_feed.record_subscriptions, ["005930"], now=clock[0])
        await runtime.dispatch_message_async(frame(clock[0]))
        clock[0] += timedelta(seconds=1)
        await runtime._reset_fujimoto_feed("disconnect")
        clock[0] += timedelta(seconds=1)
        await runtime._reset_fujimoto_feed("reconnect")
        await runtime._call_recording(runtime._recording_feed.record_subscriptions, ["005930"], now=clock[0])
        await runtime.dispatch_message_async(frame(clock[0]))
        clock[0] += timedelta(seconds=1)
        await runtime.dispatch_message_async(frame(clock[0]))
        await asyncio.gather(*list(runtime._feed_tasks))
        with Session(file_db.get_bind()) as reader:
            rows = reader.query(FujimotoEvidence).order_by(FujimotoEvidence.id).all()
            quotes = [r for r in rows if r.kind == "quote"]
            assert len(quotes) == 2
            assert not quotes[0].payload["gap"] and quotes[1].payload["gap"]
            assert [r.payload["reason"] for r in rows if r.kind == "feed_quality"] == [
                "disconnect", "reconnect", "tape_capacity_exhausted"]
            recordings = [r.payload for r in rows if r.kind == "feed_recording"]
            assert [r["kind"] for r in recordings] == ["subscriptions", "outage", "subscriptions"]
            assert recordings[1]["start"] == (clock[0] - timedelta(seconds=2)).isoformat()
            assert recordings[1]["end"] == (clock[0] - timedelta(seconds=1)).isoformat()
        assert service.feed._recorded == service.feed.capacity
    finally:
        await runtime.stop()


@pytest.mark.parametrize("timeout", [False, True])
async def test_shutdown_does_not_close_session_under_running_recorder(file_db, monkeypatch, timeout):
    import maps.limit_up.runtime as module
    runtime, _, clock = await start_recorder(file_db, monkeypatch)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    original = runtime._record_quote
    def blocked(quote):
        entered.set()
        try:
            assert release.wait(5)
            return original(quote)
        finally:
            finished.set()
    monkeypatch.setattr(runtime, "_record_quote", blocked)
    order_close, recorder_close = Mock(wraps=file_db.close), Mock(wraps=runtime._recording_db.close)
    monkeypatch.setattr(file_db, "close", order_close)
    monkeypatch.setattr(runtime._recording_db, "close", recorder_close)
    producer = asyncio.create_task(runtime.dispatch_message_async(frame(clock[0])), name="test-socket")
    runtime._tasks.append(producer)
    assert await asyncio.to_thread(entered.wait, 2)
    if timeout:
        monkeypatch.setattr(module, "_SHUTDOWN_DRAIN_SECONDS", .03)
    stopping = asyncio.create_task(runtime.stop())
    try:
        if timeout:
            await asyncio.wait_for(stopping, 2)
        else:
            await asyncio.sleep(.03)
            assert not stopping.done()
        order_close.assert_not_called()
        recorder_close.assert_not_called()
    finally:
        release.set()
        assert await asyncio.to_thread(finished.wait, 2)
        await stopping
    with Session(file_db.get_bind()) as reader:
        assert reader.query(FujimotoEvidence).filter_by(kind="quote").count() == 1
    if timeout:
        order_close.assert_not_called()
        recorder_close.assert_not_called()
        runtime._recording_db.close()
    else:
        order_close.assert_called_once()
        recorder_close.assert_called_once()


async def test_recording_failure_is_visible_and_prevents_quote_decisions(file_db, monkeypatch, caplog):
    runtime, service, clock = await start_recorder(file_db, monkeypatch)
    def broken(*args, **kwargs):
        raise RuntimeError("synthetic recorder write failure")
    monkeypatch.setattr(runtime._recording_feed, "record_quote", broken)
    decision = Mock(wraps=service.on_quote)
    monkeypatch.setattr(service, "on_quote", decision)
    try:
        with pytest.raises(RuntimeError, match="synthetic recorder write failure"):
            await runtime.dispatch_message_async(frame(clock[0]))
        with pytest.raises(RuntimeError, match="restart_required"):
            await runtime.dispatch_message_async(frame(clock[0]))
        assert "quote decisions blocked until restart" in caplog.text
        decision.assert_not_called()
        assert runtime._service_queue.empty()
    finally:
        await runtime.stop()


async def test_socket_keeps_recording_while_subscription_allocation_waits(file_db, monkeypatch):
    """The real socket loop must not wait on the execution lane for allocation."""
    runtime, _, clock = await start_recorder(file_db, monkeypatch)
    incoming = asyncio.Queue()
    socket = Mock()
    socket.recv = incoming.get
    release, thread = held_lock(account_key(runtime.settings))
    entered = threading.Event()
    def reconcile():
        entered.set()
        with account_execution_lock(account_key(runtime.settings)):
            pass
    pending = asyncio.create_task(runtime._call_service(reconcile))
    serving = asyncio.create_task(runtime._serve_socket(socket, "synthetic"))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        await runtime._subscription_queue.put("*")
        # Wait until allocation has actually joined the blocked execution queue.
        for _ in range(100):
            if runtime._service_queue.qsize():
                break
            await asyncio.sleep(.01)
        assert runtime._service_queue.qsize()
        await incoming.put(frame(clock[0]))
        for _ in range(100):
            with Session(file_db.get_bind()) as reader:
                count = reader.query(FujimotoEvidence).filter_by(kind="quote").count()
            if count:
                break
            await asyncio.sleep(.01)
        assert count == 1, "socket stopped receiving while allocation waited"
    finally:
        serving.cancel()
        await asyncio.gather(serving, return_exceptions=True)
        release.set()
        await pending
        thread.join(2)
        await runtime.stop()


@pytest.mark.parametrize("invalidation", ["elapsed", "reset"])
def test_broker_read_cannot_extend_original_quote_order_deadline(file_db, monkeypatch, invalidation):
    """A quote expired/invalidated during broker reads cannot reserve or send."""
    from types import SimpleNamespace
    from maps.common.exceptions import ExecutionBlockedError
    import maps.fujimoto.service as module
    elapsed = [0.]
    current = [True]
    monkeypatch.setattr(module, "monotonic", lambda: elapsed[0])
    now = datetime.now(timezone.utc)
    service = FujimotoService(file_db, Mock())
    key = account_key(service.settings)
    config = service.repo.configure(key, 7, Mode.SAFE, 5000000)
    cycle = service.repo.create_cycle(config.id, "AAA")
    file_db.commit()
    rule = RuleEvidence(date(2026, 10, 7), 1000, True, financial_status="maintained",
                        daily_rsi=40, weekly_rsi=50, macd_golden=True)
    service.manager._broker.get_execution_snapshot.return_value = SimpleNamespace(
        as_of=now, positions={}, balance=SimpleNamespace(total_value=10000000, cash=10000000))
    def slow_orders():
        elapsed[0] = 2. if invalidation == "elapsed" else 0.
        current[0] = invalidation != "reset"
        return []
    service.manager._broker.get_open_orders.side_effect = slow_orders
    monkeypatch.setattr(service, "authorize_order", Mock())
    monkeypatch.setattr(service.repo, "plan_buy", lambda *a, **kw: SimpleNamespace(quantity=10, limit_price=1000, stop_price=920))
    # Quote was already two seconds old at decision start: only one second remains.
    reason = "quote_stale_after_wait" if invalidation == "elapsed" else "quote_invalidated"
    with pytest.raises(ExecutionBlockedError, match=reason):
        service._submit_decision(cycle, rule, evaluate(Mode.SAFE, rule, CycleState()),
                                 1000, None, now, now + timedelta(seconds=1), lambda: current[0])
    assert service.repo.orders(key) == []
    service.manager.submit.assert_not_called()


async def test_reset_invalidates_queued_decisions_without_erasing_raw_quotes(file_db, monkeypatch):
    """A recent pre-disconnect quote remains evidence, but cannot resume a signal."""
    runtime, service, clock = await start_recorder(file_db, monkeypatch)
    release, thread = held_lock(account_key(runtime.settings))
    entered = threading.Event()
    def reconcile():
        entered.set()
        with account_execution_lock(account_key(runtime.settings)):
            pass
    pending = asyncio.create_task(runtime._call_service(reconcile))
    decision = Mock(wraps=service.on_quote)
    monkeypatch.setattr(service, "on_quote", decision)
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        await runtime.dispatch_message_async(frame(clock[0]))
        await runtime._reset_fujimoto_feed("disconnect")
        await runtime._reset_fujimoto_feed("reconnect")
        release.set()
        await pending
        await asyncio.gather(*list(runtime._feed_tasks))
        decision.assert_not_called()
        clock[0] += timedelta(seconds=1)
        await runtime.dispatch_message_async(frame(clock[0]))
        await asyncio.gather(*list(runtime._feed_tasks))
        decision.assert_called_once()
        assert decision.call_args.args[0].gap
        with Session(file_db.get_bind()) as reader:
            assert reader.query(FujimotoEvidence).filter_by(kind="quote").count() == 2
    finally:
        release.set()
        await pending
        thread.join(2)
        await runtime.stop()


def test_direct_recording_does_not_consume_candidate_admission_quote(file_db, monkeypatch):
    """Recording and freshness evaluation must not treat one arrival as a duplicate."""
    now = datetime(2026, 10, 8, 1, tzinfo=timezone.utc)
    monkeypatch.setattr("maps.fujimoto.service.utcnow", lambda: now.replace(tzinfo=None))
    monkeypatch.setattr("maps.fujimoto.sources.current_financial_status", lambda *a: "maintained")
    service = FujimotoService(file_db, Mock())
    key, repo = account_key(service.settings), service.repo
    rule = RuleEvidence(date(2026, 10, 7), 1000, True, financial_status="maintained",
                        daily_rsi=40, weekly_rsi=50, macd_golden=True)
    repo.configure(key, 7, Mode.SAFE, 5000000)
    observed = now - timedelta(days=1)
    repo.record_evidence("candidate", "AAA", observed, observed, {"rule": rule})
    repo.record_evidence("screen", "*", observed, observed, {"ranked": ["AAA"], "ref_date": rule.as_of.isoformat()})
    repo.record_evidence("control", "*", observed, observed,
                        {"execution_mode": "paper", "entries_enabled": True, "sell_consent": True}, account_key=key)
    file_db.commit()
    submitted = Mock()
    monkeypatch.setattr(service, "_submit_decision", submitted)
    service.on_quote(FeedQuote("AAA", 1001, 10, 1000, 30, 1., 10, 30, now, now), now=now)
    submitted.assert_called_once()
    assert submitted.call_args.args[2].action == "buy"


async def test_subscription_failure_preserves_frame_received_in_same_wakeup(file_db, monkeypatch):
    """Concurrent send failure cannot discard an already received raw quote."""
    from unittest.mock import AsyncMock
    runtime, _, clock = await start_recorder(file_db, monkeypatch)
    socket = Mock(recv=AsyncMock(return_value=frame(clock[0])))
    monkeypatch.setattr(runtime, "_refresh_subscriptions", AsyncMock(side_effect=RuntimeError("synthetic send failure")))
    await runtime._subscription_queue.put("*")
    try:
        with pytest.raises(RuntimeError, match="synthetic send failure"):
            await runtime._serve_socket(socket, "synthetic")
        with Session(file_db.get_bind()) as reader:
            assert reader.query(FujimotoEvidence).filter_by(kind="quote").count() == 1
    finally:
        await runtime.stop()


@pytest.mark.parametrize("invalidation", ["disconnect", "recorder_failure"])
async def test_inflight_quote_rechecks_invalidation_after_account_lock(file_db, monkeypatch, invalidation):
    """An actual quote already inside on_quote cannot resume after feed invalidation."""
    import maps.fujimoto.service as module
    runtime, service, clock = await start_recorder(file_db, monkeypatch)
    now = clock[0] = datetime(2026, 10, 8, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(module, "utcnow", lambda: now.replace(tzinfo=None))
    monkeypatch.setattr(module, "monotonic", lambda: 0.)
    monkeypatch.setattr("maps.fujimoto.sources.current_financial_status", lambda *a: "maintained")
    key, repo = account_key(service.settings), service.repo
    rule = RuleEvidence(date(2026, 10, 7), 1300, True, financial_status="maintained",
                        daily_rsi=40, weekly_rsi=50, macd_golden=True)
    source = repo.record_evidence("candidate", "005930", now - timedelta(days=1), now - timedelta(days=1), {"rule": rule})
    config = repo.configure(key, 7, Mode.SAFE, 5000000)
    cycle = repo.create_cycle(config.id, "005930")
    order = repo.reserve_order(cycle.id, evaluate(Mode.SAFE, rule, CycleState()), source.id,
                               10, 1300, signal_date=rule.as_of, stop_price=1196)
    repo.apply_fill(FillEvent(order.id, key, None, 10, 13000, 0, 0, "FILLED", now.date()))
    repo.record_evidence("control", "*", now - timedelta(minutes=1), now - timedelta(minutes=1),
                        {"execution_mode": "paper", "entries_enabled": False, "sell_consent": True}, account_key=key)
    file_db.commit()
    submitted, attempted = Mock(), threading.Event()
    monkeypatch.setattr(service, "_submit_decision", submitted)
    @contextmanager
    def watched_lock(lock_key):
        attempted.set()
        with account_execution_lock(lock_key):
            yield
    monkeypatch.setattr(module, "account_execution_lock", watched_lock)
    release, thread = held_lock(key)
    try:
        await runtime.dispatch_message_async(frame(now))
        assert await asyncio.to_thread(attempted.wait, 2)
        if invalidation == "disconnect":
            await runtime._reset_fujimoto_feed("disconnect")
        else:
            def broken(*args, **kwargs):
                raise RuntimeError("synthetic write failure")
            monkeypatch.setattr(runtime._recording_feed, "record_quote", broken)
            with pytest.raises(RuntimeError, match="synthetic write failure"):
                await runtime.dispatch_message_async(frame(now))
        release.set()
        await asyncio.gather(*list(runtime._feed_tasks))
        submitted.assert_not_called()
        assert len(repo.orders(key)) == 1  # Only the preexisting filled purchase.
    finally:
        release.set()
        thread.join(2)
        await runtime.stop()


@pytest.mark.parametrize("invalidation", ["disconnect", "recorder_failure"])
async def test_manager_rechecks_quote_after_blocked_broker_read(file_db, monkeypatch, invalidation):
    """Reset/failure during real manager buying-power I/O cannot reach place_order."""
    from maps.common.exceptions import ExecutionBlockedError
    from maps.common.models import AccountObservation, ExecutionAccountState, OrderIntent
    from maps.execution.broker_adapter import Order, OrderSide, OrderType
    from maps.execution.mock_broker import MockBroker
    from maps.execution.order_manager import OrderManager
    from maps.execution.safety import ExecutionContext
    from maps.risk.manager import RiskConfig, RiskManager
    runtime, _, clock = await start_recorder(file_db, monkeypatch)
    with Session(file_db.get_bind()) as manager_db:
        broker = MockBroker(1000000, {"AAA": 1000})
        manager = OrderManager(broker, RiskManager(broker, manager_db, config=RiskConfig(position_size_limit=.5)),
                               manager_db, settings=runtime.settings, notifier=Mock(send=Mock(return_value=False)))
        manager.sync_broker_state()
        observation = manager_db.query(AccountObservation).one()
        observation.ref_date -= timedelta(days=1)
        observation.observed_at -= timedelta(days=1)
        manager_db.get(ExecutionAccountState, account_key(runtime.settings)).ref_date = observation.ref_date
        manager_db.commit()
        entered, release = threading.Event(), threading.Event()
        buying_power = broker.get_buying_power
        def blocked_power(order):
            entered.set()
            assert release.wait(5)
            return buying_power(order)
        monkeypatch.setattr(broker, "get_buying_power", blocked_power)
        place_order = Mock(wraps=broker.place_order)
        monkeypatch.setattr(broker, "place_order", place_order)
        epoch = runtime._recording_epoch
        now = datetime.now(timezone.utc)
        context = ExecutionContext("inflight-read", source="mock", valid_until=now + timedelta(seconds=3),
            quote_is_current=lambda: not runtime._recording_failed and epoch == runtime._recording_epoch)
        order = Order("test", "AAA", OrderSide.BUY, OrderType.LIMIT, 10, limit_price=1000)
        pending = asyncio.create_task(asyncio.to_thread(manager.submit, order, context=context))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            if invalidation == "disconnect":
                await runtime._reset_fujimoto_feed("disconnect")
            else:
                def broken(*args, **kwargs):
                    raise RuntimeError("synthetic write failure")
                monkeypatch.setattr(runtime._recording_feed, "record_quote", broken)
                with pytest.raises(RuntimeError, match="synthetic write failure"):
                    await runtime.dispatch_message_async(frame(clock[0]))
            release.set()
            with pytest.raises(ExecutionBlockedError, match="quote_invalidated"):
                await pending
            assert datetime.now(timezone.utc) < context.valid_until
            place_order.assert_not_called()
            manager_db.expire_all()
            intent = manager_db.query(OrderIntent).filter_by(event_key="inflight-read").one()
            assert intent.status == "PREPARED" and intent.broker_order_id is None
            assert "quote_is_current" not in intent.request
            assert intent.valid_until == context.valid_until.replace(tzinfo=None)
        finally:
            release.set()
            await asyncio.gather(pending, return_exceptions=True)
            await runtime.stop()
