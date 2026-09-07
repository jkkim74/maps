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
    assert engine_active_at(dt.datetime(2026, 8, 28, 15, 35, tzinfo=KST))
    # 15:28 강제청산이 늦게 들어와도 처리할 여유를 둔다
    assert not engine_active_at(dt.datetime(2026, 8, 28, 15, 40, 1, tzinfo=KST))
    assert not engine_active_at(dt.datetime(2026, 8, 28, 22, 0, tzinfo=KST))


def test_index_guard_runs_only_during_the_krx_regular_session() -> None:
    """Next-open/EOD work stays live outside the narrower index window."""
    import maps.limit_up.runtime as runtime_module

    assert hasattr(runtime_module, "index_guard_active_at")
    index_guard_active_at = runtime_module.index_guard_active_at
    assert not index_guard_active_at(dt.datetime(2026, 8, 28, 8, 59, 59, tzinfo=KST))
    assert index_guard_active_at(dt.datetime(2026, 8, 28, 9, 0, tzinfo=KST))
    assert index_guard_active_at(dt.datetime(2026, 8, 28, 15, 30, tzinfo=KST))
    assert not index_guard_active_at(dt.datetime(2026, 8, 28, 15, 30, 1, tzinfo=KST))
    assert not index_guard_active_at(dt.datetime(2026, 8, 29, 10, 0, tzinfo=KST))


def test_engine_hours_cover_both_daily_action_windows() -> None:
    """08:59:30 next-open exits and the 15:18-15:28 overnight review must fit."""
    assert engine_active_at(dt.datetime(2026, 8, 28, 8, 59, 30, tzinfo=KST))
    assert engine_active_at(dt.datetime(2026, 8, 28, 15, 18, tzinfo=KST))
    assert engine_active_at(dt.datetime(2026, 8, 28, 15, 28, tzinfo=KST))


def _control_loop_runtime(wall: dt.datetime, index_error: Exception | None = None):
    """Build one controlled control-loop iteration without broker I/O."""
    from maps.limit_up.runtime import KISIntradayRuntime

    class _Adapter:
        def __init__(self) -> None:
            self.index_calls = 0

        def get_kosdaq_index(self) -> float:
            self.index_calls += 1
            if index_error is not None:
                raise index_error
            return 1_000.0

    class _Service:
        def __init__(self) -> None:
            self.kosdaq_calls = 0

        def tick(self, **_kwargs: object) -> None:
            return None

        def on_kosdaq(self, **_kwargs: object) -> None:
            self.kosdaq_calls += 1

    class _Deadman:
        def __init__(self) -> None:
            self.unhealthy = 0

        def ping(self, healthy: bool = True) -> None:
            if not healthy:
                self.unhealthy += 1

    runtime = object.__new__(KISIntradayRuntime)
    adapter = _Adapter()
    service = _Service()
    runtime.adapter = adapter
    runtime.service = service
    runtime.deadman = _Deadman()
    runtime.iterations = 0
    runtime.daily_actions_iteration = 0
    runtime._stop = asyncio.Event()
    runtime.monotonic = lambda: 2.0
    runtime._last_scan_at = 2.0
    runtime._last_index_at = 0.0
    runtime._last_deadman_at = 2.0

    # A failing iteration must still terminate the test, so cap the loop here
    # rather than relying on _run_daily_actions being reached.
    walls = iter((wall, wall, wall))

    def _wall_now() -> dt.datetime:
        runtime.iterations += 1
        try:
            return next(walls)
        except StopIteration:
            runtime._stop.set()
            return wall

    runtime.wall_now = _wall_now

    async def _call_service(callable_, *args: object, **kwargs: object) -> object:
        kwargs.pop("priority")
        return callable_(*args, **kwargs)

    async def _finish_iteration(*_args: object, **_kwargs: object) -> None:
        runtime.daily_actions_iteration = runtime.iterations
        runtime._stop.set()

    async def _fallback_once() -> None:
        return None

    runtime._call_service = _call_service
    runtime._run_daily_actions = _finish_iteration
    runtime.fallback_once = _fallback_once
    return runtime, adapter, service


async def test_control_loop_skips_index_polling_before_regular_session() -> None:
    """The broader engine window must not invoke KIS before the 09:00 open."""
    runtime, adapter, service = _control_loop_runtime(
        dt.datetime(2026, 8, 28, 8, 59, 59, tzinfo=KST)
    )

    await runtime._control_loop()

    assert adapter.index_calls == 0
    assert service.kosdaq_calls == 0


async def test_control_loop_polls_and_dispatches_once_during_regular_session() -> None:
    """Characterizes the intended in-session KOSDAQ polling behavior.

    Disabling polling entirely would leave the market guard stale and fail this
    controlled 09:00--15:30 KST iteration.
    """
    runtime, adapter, service = _control_loop_runtime(
        dt.datetime(2026, 8, 28, 9, 0, tzinfo=KST)
    )

    await runtime._control_loop()

    assert adapter.index_calls == 1
    assert service.kosdaq_calls == 1


async def test_control_loop_survives_a_failed_index_poll() -> None:
    """A missing index value must not cancel the rest of the iteration.

    KIS serves the guard from a 60-second bucket endpoint, so the first bucket
    of the day does not exist yet at 09:00:00 and comes back empty. Letting
    that abort the iteration skips the EOD stages and the fallback sweep, and
    reports the engine dead to the deadman monitor.
    """
    from maps.common.exceptions import BrokerAdapterError

    runtime, adapter, service = _control_loop_runtime(
        dt.datetime(2026, 8, 28, 9, 0, tzinfo=KST),
        index_error=BrokerAdapterError("KIS KOSDAQ index response was empty"),
    )

    await runtime._control_loop()

    assert adapter.index_calls == 1
    assert service.kosdaq_calls == 0
    assert runtime.daily_actions_iteration == 1
    assert runtime.deadman.unhealthy == 0


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


def test_late_arrival_still_runs_the_earlier_eod_stages() -> None:
    """Stages are deadlines: entering at 15:26 must not skip the trim entirely.

    ``confirm`` only looks at EOD_TRIM sessions, so a skipped cap reports zero
    and the carry goes overnight with no sizing applied.
    """
    import inspect

    from maps.limit_up.runtime import KISIntradayRuntime

    source = inspect.getsource(KISIntradayRuntime._run_daily_actions)
    assert 'if stage in {"cap", "confirm", "force"}:' in source
    assert 'if stage in {"confirm", "force"}' in source


def test_overnight_cap_is_not_latched_to_one_pass() -> None:
    """A session reaching OVERNIGHT after 15:18 must still get sized.

    ``force_overnight_cap`` excludes OVERNIGHT from its stranded set, so a
    once-per-day cap leaves that carry with no limit at all.
    """
    import inspect

    from maps.limit_up.runtime import KISIntradayRuntime

    source = inspect.getsource(KISIntradayRuntime._run_daily_actions)
    cap_call = source[source.index("apply_overnight_cap")::]
    assert "if wall.date() not in self._overnight_capped" not in source[:source.index("apply_overnight_cap")][-300:]
    assert cap_call  # 캡은 매 회차 멱등하게 돈다


def _scan_row(ticker: str, *, halted: bool = False) -> dict:
    """Build one broker candidate row shaped like the KIS scan response."""
    return {
        "ticker": ticker,
        "market": "KOSDAQ",
        "upper_limit_price": 1_300,
        "total_listed_shares": 10_000_000,
        "current_price": 1_250,
        "change_rate": 29.9,
        "trading_halted": halted,
    }


def _scan_runtime(db, rows: list[dict], ranked_count: int, listed: set[str]):
    """Build a scan-only runtime over the real service and its real gates."""
    from maps.limit_up.domain import LimitUpConfig
    from maps.limit_up.repository import LimitUpRepository
    from maps.limit_up.runtime import KISIntradayRuntime
    from maps.limit_up.service import LimitUpMode, LimitUpService

    for ticker in listed:
        db.add(
            SecurityMetadata(
                ticker=ticker,
                name=f"테스트{ticker}",
                market="KOSDAQ",
                security_type="STOCK",
                listing_date=dt.date(2020, 1, 1),
            )
        )
    db.commit()

    class _Adapter:
        def get_limit_up_candidates(self):
            return list(rows), ranked_count

    runtime = object.__new__(KISIntradayRuntime)
    runtime.db = db
    runtime.adapter = _Adapter()
    runtime.service = LimitUpService(
        mode=LimitUpMode.RECOMMEND_ONLY,
        config=LimitUpConfig(),
        repository=LimitUpRepository(db),
        worker=None,
    )
    runtime._subscription_queue = asyncio.Queue()
    runtime._last_scan_summary = None
    runtime.wall_now = lambda: dt.datetime(2026, 8, 28, 10, 0, tzinfo=KST)

    async def _call_service(callable_, *args: object, **kwargs: object) -> object:
        kwargs.pop("priority", None)
        return callable_(*args, **kwargs)

    runtime._call_service = _call_service
    return runtime


async def test_scan_reports_the_rank_size_and_every_reject_reason(db, caplog) -> None:
    """A quiet scan must say whether it saw nothing or rejected everything.

    Without the rank count and the per-gate tally, "no stock rose 25% today"
    and "the scan pipeline is broken" produce identical silence.
    """
    rows = [_scan_row("111111"), _scan_row("222222"), _scan_row("333333", halted=True)]
    runtime = _scan_runtime(db, rows, 42, listed={"111111", "333333"})

    with caplog.at_level("INFO", logger="maps.limit_up.runtime"):
        accepted = await runtime.scan_once()

    assert accepted == 1
    message = caplog.messages[-1]
    assert "순위 42건" in message
    assert "후보 3건" in message
    assert "신규감시 1건" in message
    assert "탈락 2건" in message
    # 222222 는 security_metadata 에 없다 — 사유가 세분화되어야 데이터 공백과 구별된다.
    assert "ineligible_security:unknown_security=1" in message
    assert "halted=1" in message


async def test_scan_summary_is_logged_once_until_it_changes(db, caplog) -> None:
    """A 5s scan loop must not repeat an unchanged summary 4,000 times a day."""
    runtime = _scan_runtime(db, [_scan_row("111111")], 7, listed=set())

    with caplog.at_level("INFO", logger="maps.limit_up.runtime"):
        await runtime.scan_once()
        first = len(caplog.messages)
        await runtime.scan_once()

    assert first == 1
    assert len(caplog.messages) == 1


async def test_scan_reports_an_empty_rank_response(db, caplog) -> None:
    """Zero ranked rows is the signature of a broken scan, not a quiet market."""
    runtime = _scan_runtime(db, [], 0, listed=set())

    with caplog.at_level("INFO", logger="maps.limit_up.runtime"):
        await runtime.scan_once()

    assert "순위 0건" in caplog.messages[-1]
    assert "후보 0건" in caplog.messages[-1]


async def test_scan_outside_entry_hours_logs_nothing(db, caplog) -> None:
    """The closed-hours early return must not blank out the day's last summary."""
    runtime = _scan_runtime(db, [_scan_row("111111")], 7, listed={"111111"})
    runtime.wall_now = lambda: dt.datetime(2026, 8, 28, 15, 0, tzinfo=KST)

    with caplog.at_level("INFO", logger="maps.limit_up.runtime"):
        assert await runtime.scan_once() == 0

    assert caplog.messages == []
    assert runtime._last_scan_summary is None


def test_a_real_runtime_starts_with_no_scan_summary(db) -> None:
    """The scan tally lives on the runtime, so its constructor must seed it.

    Every other scan test builds the runtime with ``object.__new__``, which
    skips ``__init__`` — this is the only place a missing initializer shows up
    as a failure instead of a production AttributeError on the first scan.
    """
    from types import SimpleNamespace

    from maps.limit_up.runtime import KISIntradayRuntime

    runtime = KISIntradayRuntime(
        settings=SimpleNamespace(maps_limit_up_healthchecks_ping_url=""),
        db=db,
        adapter=object(),
        service=object(),
    )

    assert runtime._last_scan_summary is None


# --- 자격 사유 세분화 (2026-09-07) ------------------------------------------------
#
# 운영 security_metadata.listing_date 가 전부 NULL 이라 3주간 후보가 전원 탈락했는데,
# 로그는 ineligible_security 하나였다. "상장일 데이터가 없다" 와 "정말 신규상장/우선주다"
# 를 같은 키로 세면 데이터 공백이 정상 탈락으로 위장한다.


def test_ineligibility_reason_names_each_gate() -> None:
    from maps.limit_up.runtime import v1_ineligibility_reason

    as_of = dt.date(2026, 9, 7)

    def _sec(**overrides):
        base = dict(
            ticker="005930",
            name="삼성전자",
            market="KOSPI",
            security_type="STOCK",
            listing_date=dt.date(1975, 6, 11),
        )
        base.update(overrides)
        return SecurityMetadata(**base)

    assert v1_ineligibility_reason(None, as_of=as_of) == "unknown_security"
    assert v1_ineligibility_reason(_sec(market="KONEX"), as_of=as_of) == "not_common_stock"
    assert v1_ineligibility_reason(_sec(security_type="SPAC"), as_of=as_of) == "not_common_stock"
    assert v1_ineligibility_reason(_sec(listing_date=None), as_of=as_of) == "listing_unknown"
    assert v1_ineligibility_reason(_sec(listing_date=as_of - dt.timedelta(days=20)), as_of=as_of) == "too_new"
    assert v1_ineligibility_reason(_sec(name="삼성전자우"), as_of=as_of) == "preferred"
    assert v1_ineligibility_reason(_sec(), as_of=as_of) is None
    # 기존 bool 진입점은 사유 함수의 얇은 포장이어야 한다.
    assert is_v1_eligible_security(_sec(listing_date=None), as_of=as_of) is False


async def test_scan_reports_a_missing_listing_date_as_its_own_reason(db, caplog) -> None:
    """상장일 NULL 은 정상 탈락이 아니라 데이터 공백이다 — 로그에 그렇게 보여야 한다."""
    runtime = _scan_runtime(db, [_scan_row("444444")], 30, listed=set())
    db.add(
        SecurityMetadata(
            ticker="444444",
            name="상장일모름",
            market="KOSDAQ",
            security_type="STOCK",
            listing_date=None,
        )
    )
    db.commit()

    with caplog.at_level("INFO", logger="maps.limit_up.runtime"):
        accepted = await runtime.scan_once()

    assert accepted == 0
    assert "ineligible_security:listing_unknown=1" in caplog.messages[-1]


async def test_control_loop_absorbs_a_broker_error_from_the_scan(caplog) -> None:
    """KIS 한도초과(EGW00201) 로 스캔이 깨져도 그 회차의 EOD·폴백은 계속 돈다.

    2026-09-04 09:21 실제 발생 — 지수 폴링은 이미 WARNING 으로 흡수하는데 스캔만
    바깥 except 로 흘러 ERROR 와 함께 이터레이션 전체를 버렸다.
    """
    from maps.common.exceptions import BrokerAdapterError

    wall = dt.datetime(2026, 8, 28, 10, 0, tzinfo=KST)
    runtime, _adapter, _service = _control_loop_runtime(wall)
    runtime._last_scan_at = -10.0

    async def _broken_scan() -> int:
        raise BrokerAdapterError("KIS transient HTTP 500: EGW00201")

    runtime.scan_once = _broken_scan

    with caplog.at_level("WARNING", logger="maps.limit_up.runtime"):
        await runtime._control_loop()

    assert runtime.daily_actions_iteration == 1
    assert runtime.deadman.unhealthy == 0
    warnings = [r for r in caplog.records if r.levelname == "WARNING" and "스캔 실패" in r.getMessage()]
    assert len(warnings) == 1
    assert not [r for r in caplog.records if r.levelname == "ERROR"]


def test_feed_disconnect_logging_splits_expected_closes_from_faults(caplog) -> None:
    """정각마다 오는 ConnectionClosed 는 WARNING 한 줄, 설정·네트워크 결함은 ERROR+traceback.

    9/1~9/7 매 거래일 09:00~16:00 정각에 KIS 가 소켓을 닫았고(시간당 1회), 재연결은
    2~6초 만에 됐는데 로그는 매일 ERROR 8건 + Traceback 이었다.
    """
    from websockets.exceptions import ConnectionClosedError

    from maps.limit_up.runtime import _log_feed_disconnect

    with caplog.at_level("WARNING", logger="maps.limit_up.runtime"):
        _log_feed_disconnect(ConnectionClosedError(None, None), 1.0)
        _log_feed_disconnect(ConnectionError("upper-limit feed silent past timeout"), 2.0)
        _log_feed_disconnect(OSError("dns failure"), 4.0)
        _log_feed_disconnect(TimeoutError("timed out during opening handshake"), 8.0)

    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert len(warnings) == 2
    assert all(r.exc_info is None and "끊김" in r.getMessage() for r in warnings)
    assert len(errors) == 2
    assert all(r.exc_info is not None for r in errors)
