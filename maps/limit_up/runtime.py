"""KIS real-time lifecycle orchestration for the upper-limit V1 engine."""

from __future__ import annotations

import asyncio
import datetime as dt
import itertools
import json
import logging
import time
from collections.abc import Callable
from typing import Any

import requests
import websockets
from sqlalchemy.orm import Session

from maps.common.models import SecurityMetadata
from maps.common.settings import MapsSettings
from maps.execution.kis_adapter import KISAdapter
from maps.limit_up.domain import LimitUpConfig, LimitUpState
from maps.limit_up.feed import FeedQuote, FeedTrade, RestFallbackLimiter, parse_kis_ws_message
from maps.limit_up.service import (
    Candidate,
    LimitUpMode,
    LimitUpService,
    automatic_mode_blocked_reason,
)
from maps.market.trading_rules import is_krx_closed_date


logger = logging.getLogger(__name__)
KST = dt.timezone(dt.timedelta(hours=9))
_TRADE_TR_ID = "H0STCNT0"
_QUOTE_TR_ID = "H0STASP0"


def subscription_payload(approval_key: str, tr_id: str, ticker: str) -> str:
    """Build one official KIS real-time subscription envelope."""
    if tr_id not in {_TRADE_TR_ID, _QUOTE_TR_ID}:
        raise ValueError("unsupported upper-limit realtime TR ID")
    return json.dumps(
        {
            "header": {
                "approval_key": approval_key,
                "custtype": "P",
                "tr_type": "1",
                "content-type": "utf-8",
            },
            "body": {"input": {"tr_id": tr_id, "tr_key": ticker}},
        },
        separators=(",", ":"),
    )


# 엔진이 실제로 일하는 시간대. 08:59:30 익일 시가 청산과 15:18~15:28 오버나이트 심사를
# 모두 감싼다. 이 밖에서는 브로커를 부르지 않는다 — 24시간 도는 폴링은 그 자체로 사고다.
_ENGINE_OPEN = dt.time(8, 50)
_ENGINE_CLOSE = dt.time(15, 35)
_IDLE_SLEEP_SECONDS = 30.0
# 보호 작업(타이머·지수 래치·피드 상실·EOD 청산)이 진입 작업보다 먼저 실행된다.
_PRIORITY_HIGH = 0
_PRIORITY_NORMAL = 10
_RECONNECT_BACKOFF_MAX = 60.0
# 이 시간 동안 어떤 프레임도 안 오면 죽은 연결로 본다. KIS 는 장중 체결이 없어도
# PINGPONG 을 보내므로 완전한 무음은 정상이 아니다.
_FEED_SILENCE_TIMEOUT_SECONDS = 60.0
# 종료 시 진행 중인 브로커 작업을 기다리는 상한.
_SHUTDOWN_DRAIN_SECONDS = 30.0
# 시세 처리 적체 경고 임계. 넘으면 브로커 응답이 느리다는 신호다.
_FEED_BACKLOG_ALERT = 50
# 관리자 API 가 엔진 응답을 기다리는 상한.
_ADMIN_CALL_TIMEOUT_SECONDS = 10.0


def engine_active_at(wall: dt.datetime) -> bool:
    """Return whether the intraday engine should be doing live broker work.

    Args:
        wall: KST wall-clock time.

    Returns:
        ``True`` only on a KRX trading day inside the engine's hours.
    """
    if is_krx_closed_date(wall.date()):
        return False
    return _ENGINE_OPEN <= wall.time().replace(tzinfo=None) <= _ENGINE_CLOSE


# 15:18 심사·트림 → 15:25 재확인 → 15:28 포기.
# **창이 아니라 하한선이다.** 좁은 창으로 두면 그 몇 분 사이 재시작·장애가 나는 것만으로
# 상한 미적용 포지션이 익일로 넘어간다 — "절대 상한" 이 프로세스 가동 여부에 의존하게 된다.
# 하루 한 번 래치가 중복 실행을 막으므로, 지났는데 아직 안 했으면 늦게라도 실행한다.
_EOD_STAGES: tuple[tuple[str, dt.time], ...] = (
    ("force", dt.time(15, 28)),
    ("confirm", dt.time(15, 25)),
    ("cap", dt.time(15, 18)),
)


def _log_feed_task_error(task: "asyncio.Future") -> None:
    """Surface a failed fire-and-forget feed task instead of losing it."""
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        logger.error("Upper-limit feed event failed", exc_info=error)


def feed_is_silent(*, last_frame_at: float, now: float) -> bool:
    """Return whether the socket has gone quiet long enough to be considered dead.

    Args:
        last_frame_at: Monotonic time of the last frame received.
        now: Current monotonic time.

    Returns:
        ``True`` once the silence exceeds the timeout.
    """
    return now - last_frame_at >= _FEED_SILENCE_TIMEOUT_SECONDS


def eod_stage(clock: dt.time) -> str | None:
    """Return the latest overnight checkpoint that is due at ``clock``.

    Later stages win: a process starting at 15:29 must go straight to ``force``
    rather than replay a trim that can no longer fill.

    Args:
        clock: Naive KST wall time.

    Returns:
        ``cap``, ``confirm``, ``force``, or ``None`` before 15:18.
    """
    for name, start in _EOD_STAGES:
        if clock >= start:
            return name
    return None


def is_v1_eligible_security(
    security: SecurityMetadata | None, *, as_of: dt.date
) -> bool:
    """Return the fail-closed V1 common-share and listing-age verdict."""
    if security is None:
        return False
    if security.market not in {"KOSPI", "KOSDAQ"} or security.security_type != "STOCK":
        return False
    if security.listing_date is None or (as_of - security.listing_date).days < 100:
        return False
    name = security.name.replace(" ", "").upper()
    preferred_markers = ("우", "우B", "우C", "1우", "2우", "3우")
    return not any(name.endswith(marker.upper()) for marker in preferred_markers)


class DeadmanMonitor:
    """Minimal Healthchecks.io sender whose representation never leaks its URL."""

    def __init__(
        self,
        ping_url: str,
        *,
        sender: Callable[[str], Any] | None = None,
    ) -> None:
        """Configure the opaque ping endpoint and injectable HTTP boundary."""
        self._ping_url = ping_url.rstrip("/")
        self._sender = sender or self._send

    def __repr__(self) -> str:
        """Return a secret-free diagnostic representation."""
        return f"DeadmanMonitor(configured={bool(self._ping_url)})"

    def ping(self, *, healthy: bool) -> bool:
        """Send success or failure once; return false when unconfigured/failed."""
        if not self._ping_url:
            return False
        target = self._ping_url if healthy else f"{self._ping_url}/fail"
        try:
            self._sender(target)
        except Exception:  # noqa: BLE001 - monitoring must never stop exits
            logger.exception("Upper-limit deadman ping failed")
            return False
        return True

    @staticmethod
    def _send(url: str) -> None:
        """Issue one bounded Healthchecks GET without logging the target."""
        response = requests.get(url, timeout=5)
        response.raise_for_status()


class KISIntradayRuntime:
    """Run scanning, WebSocket dispatch, timers, fallbacks, and daily schedules."""

    def __init__(
        self,
        *,
        settings: MapsSettings,
        db: Session,
        adapter: KISAdapter,
        service: LimitUpService,
        monotonic: Callable[[], float] = time.monotonic,
        wall_now: Callable[[], dt.datetime] | None = None,
    ) -> None:
        """Bind one process-local service and its broker/DB resources."""
        self.settings = settings
        self.db = db
        self.adapter = adapter
        self.service = service
        self.monotonic = monotonic
        self.wall_now = wall_now or (lambda: dt.datetime.now(KST))
        self.deadman = DeadmanMonitor(settings.maps_limit_up_healthchecks_ping_url)
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task[Any]] = []
        self._subscription_queue: asyncio.Queue[str] = asyncio.Queue()
        self._subscribed: set[str] = set()
        self._quotes: dict[str, FeedQuote] = {}
        self._feed_connected = False
        self._last_scan_at = 0.0
        self._last_index_at = 0.0
        self._last_deadman_at = 0.0
        self._eod_reviewed: set[tuple[dt.date, str]] = set()
        self._overnight_capped: set[dt.date] = set()
        self._overnight_confirmed: set[dt.date] = set()
        self._overnight_forced: set[dt.date] = set()
        self._opening_submitted: set[tuple[dt.date, str]] = set()
        self._fallback_limiter = RestFallbackLimiter(min_interval_seconds=0.5)
        self._last_frame_at = 0.0
        self._feed_tasks: set[asyncio.Future] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._service_queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self._service_sequence = itertools.count()

    async def _call_service(
        self,
        fn: Callable[..., Any],
        *args: Any,
        priority: int = _PRIORITY_NORMAL,
        **kwargs: Any,
    ) -> Any:
        """Queue one service call for the serialized worker and await its result.

        Every service call can reach the broker over HTTP. Running that inline
        froze the event loop: a slow KIS order response stopped WebSocket reads,
        so a *different* ticker's stop-loss tick simply never arrived — the tape
        kept flowing but nothing was listening.

        Protective work (timers, index halt, feed loss, every EOD exit) is queued
        ahead of entry work, so a slow buy cannot delay a sell. Equal priorities
        keep submission order, which the state machine requires, and only one
        thread ever touches the ORM session — SQLAlchemy sessions tolerate
        sequential cross-thread use but never concurrent use.

        Args:
            fn: Synchronous service method to run.
            *args: Positional arguments for ``fn``.
            priority: Lower runs first; protective work uses ``_PRIORITY_HIGH``.
            **kwargs: Keyword arguments for ``fn``.

        Returns:
            Whatever ``fn`` returned.
        """
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        await self._service_queue.put(
            (priority, next(self._service_sequence), fn, args, kwargs, future)
        )
        return await future

    async def _service_pump(self) -> None:
        """Run queued service calls one at a time, protective work first.

        Deliberately does **not** watch ``_stop``: shutdown sets that flag first,
        so a pump that exited on it would leave the queue unserved and every
        drain would sit out its timeout. ``stop()`` cancels this task once the
        queue is empty.
        """
        while True:
            priority, _, fn, args, kwargs, future = await self._service_queue.get()
            del priority
            try:
                result = await asyncio.to_thread(fn, *args, **kwargs)
            except asyncio.CancelledError:
                if not future.done():
                    future.cancel()
                raise
            except Exception as exc:  # noqa: BLE001 - surfaced to the caller
                if not future.done():
                    future.set_exception(exc)
            else:
                if not future.done():
                    future.set_result(result)
            finally:
                self._service_queue.task_done()

    async def start(self) -> None:
        """Recover broker truth before starting all background loops."""
        now = self.wall_now()
        # 펌프가 아직 없으므로 큐에 넣으면 영원히 대기한다. 이 시점에는 경합할 상대도
        # 없으니 직접 스레드로 돌린다.
        await asyncio.to_thread(
            self.service.recover,
            ref_date=now.date(),
            now_monotonic=self.monotonic(),
            now_kst=now,
        )
        self._loop = asyncio.get_running_loop()
        self._tasks = [
            asyncio.create_task(self._service_pump(), name="limit-up-service"),
            asyncio.create_task(self._control_loop(), name="limit-up-control"),
            asyncio.create_task(self._websocket_loop(), name="limit-up-websocket"),
        ]

    async def stop(self) -> None:
        """Stop runtime loops while leaving persisted broker state recoverable.

        Producers are cancelled first, then the queue is drained before the pump
        goes and the session closes. ``asyncio.to_thread`` work is **not**
        cancellable: closing the session under a running order would abort a
        broker call mid-flight and leave the database write half-done.
        """
        self._stop.set()
        producers = [t for t in self._tasks if t.get_name() != "limit-up-service"]
        for task in producers:
            task.cancel()
        if producers:
            await asyncio.gather(*producers, return_exceptions=True)
        # 아직 큐에 들어가지 못한 시세 작업이 있으면 드레인이 그것들을 못 본다.
        if self._feed_tasks:
            await asyncio.gather(*list(self._feed_tasks), return_exceptions=True)
        try:
            await asyncio.wait_for(
                self._service_queue.join(), timeout=_SHUTDOWN_DRAIN_SECONDS
            )
        except (asyncio.TimeoutError, TimeoutError):
            logger.warning(
                "상한가 V1 종료 — 진행 중 브로커 작업이 %.0f초 안에 안 끝났다",
                _SHUTDOWN_DRAIN_SECONDS,
            )
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self.db.close()

    def _load_security(self, ticker: str) -> SecurityMetadata | None:
        """Read one security row on the serialized worker, never the event loop."""
        return (
            self.db.query(SecurityMetadata)
            .filter(SecurityMetadata.ticker == ticker)
            .one_or_none()
        )

    async def scan_once(self) -> int:
        """Discover broker candidates and subscribe newly accepted common shares."""
        now = self.wall_now()
        if not dt.time(9, 10) <= now.time().replace(tzinfo=None) <= dt.time(14, 30):
            return 0
        rows = await asyncio.to_thread(self.adapter.get_limit_up_candidates)
        accepted = 0
        for row in rows:
            ticker = str(row["ticker"])
            # 같은 ORM 세션을 펌프 스레드가 쓰고 있다. 이벤트 루프에서 직접 조회하면
            # 스캔과 주문 처리가 겹치는 순간 동시 사용이 된다.
            security = await self._call_service(self._load_security, ticker)
            eligible = is_v1_eligible_security(security, as_of=now.date()) and not bool(
                row.get("trading_halted", False)
            )
            candidate = Candidate(
                ticker=ticker,
                market=str(row["market"]),
                upper_limit_price=int(row["upper_limit_price"]),
                total_listed_shares=int(row["total_listed_shares"]),
                current_price=int(row["current_price"]),
                change_rate=float(row["change_rate"]),
                eligible=eligible,
            )
            if await self._call_service(
                self.service.watch_candidate, candidate, now_kst=now
            ):
                accepted += 1
                await self._subscription_queue.put(ticker)
        return accepted

    def dispatch_message(self, raw: str, *, received_at: float | None = None) -> int:
        """Normalize one WebSocket frame and dispatch it to the shared FSM.

        Synchronous path, used by tests and any caller already off the loop.
        The live socket uses :meth:`dispatch_message_async` so a broker call
        inside the FSM cannot stall WebSocket reads.
        """
        at = self.monotonic() if received_at is None else received_at
        now = self.wall_now()
        for event in parse_kis_ws_message(raw, received_at=at):
            self._apply_feed_event(event, now)
        return 1 if raw else 0

    async def dispatch_message_async(
        self, raw: str, *, received_at: float | None = None
    ) -> int:
        """Queue one frame's effects and return without waiting for them.

        Awaiting the service work here made the socket read the *next* frame only
        after the previous one finished — so a slow buy response held back every
        stop-loss tick behind it. The queue already preserves order, so the read
        loop does not need to wait; it only needs to hand work over.
        """
        at = self.monotonic() if received_at is None else received_at
        events = parse_kis_ws_message(raw, received_at=at)
        now = self.wall_now()
        for event in events:
            task = asyncio.ensure_future(
                self._call_service(self._apply_feed_event, event, now)
            )
            # 추적하지 않으면 종료 시 아직 큐에 들어가지도 않은 작업이 남은 채
            # queue.join() 이 먼저 끝나고 DB 세션이 닫힌다.
            self._feed_tasks.add(task)
            task.add_done_callback(self._feed_tasks.discard)
            task.add_done_callback(_log_feed_task_error)
        if len(self._feed_tasks) >= _FEED_BACKLOG_ALERT:
            logger.warning(
                "상한가 시세 처리 적체 %d건 — 브로커 응답이 느리다", len(self._feed_tasks)
            )
        return len(events)

    def _apply_feed_event(self, event: Any, now: dt.datetime) -> None:
        """Apply one normalized feed event to the state machine."""
        if isinstance(event, FeedTrade):
            self.service.on_trade(event, now_kst=now)
        else:
            self._quotes[event.ticker] = event
            self.service.on_quote(event, now_kst=now)

    async def fallback_once(self) -> None:
        """Poll held prices only after feed loss and apply protection-only events."""
        if self._feed_connected:
            return
        now_mono = self.monotonic()
        delay = self._fallback_limiter.delay(now=now_mono)
        if delay > 0:
            await asyncio.sleep(delay)
        tickers = self.service.held_tickers()
        if not tickers:
            return
        try:
            prices = await asyncio.to_thread(self.adapter.get_current_prices, tickers)
        except Exception as exc:  # noqa: BLE001 - degraded mode keeps retrying slowly
            if "429" in str(exc) or "EGW00201" in str(exc):
                self._fallback_limiter.record_rate_limit()
            logger.warning("Upper-limit REST fallback failed: %s", type(exc).__name__)
            return
        self._fallback_limiter.record_call(now=self.monotonic())
        self._fallback_limiter.record_success()
        wall = self.wall_now()
        for ticker, price in prices.items():
            await self._call_service(
                self.service.on_fallback_price,
                ticker,
                price=int(price),
                at=self.monotonic(),
                now_kst=wall,
                priority=_PRIORITY_HIGH,
            )

    async def _control_loop(self) -> None:
        """Run low-rate scanning, timers, index guard, EOD, and deadman work."""
        while not self._stop.is_set():
            now_mono = self.monotonic()
            wall = self.wall_now()
            if not engine_active_at(wall):
                if now_mono - self._last_deadman_at >= 60.0:
                    self._last_deadman_at = now_mono
                    await asyncio.to_thread(self.deadman.ping, healthy=True)
                await asyncio.sleep(_IDLE_SLEEP_SECONDS)
                continue
            try:
                await self._call_service(
                    self.service.tick,
                    now_monotonic=now_mono,
                    now_kst=wall,
                    priority=_PRIORITY_HIGH,
                )
                if now_mono - self._last_scan_at >= 5.0:
                    self._last_scan_at = now_mono
                    await self.scan_once()
                if now_mono - self._last_index_at >= 1.0:
                    self._last_index_at = now_mono
                    value = await asyncio.to_thread(self.adapter.get_kosdaq_index)
                    await self._call_service(
                        self.service.on_kosdaq,
                        value=value,
                        at=now_mono,
                        now_kst=wall,
                        priority=_PRIORITY_HIGH,
                    )
                await self._run_daily_actions(wall)
                await self.fallback_once()
                if now_mono - self._last_deadman_at >= 60.0:
                    self._last_deadman_at = now_mono
                    await asyncio.to_thread(
                        self.deadman.ping,
                        healthy=not self.service.control_lost(),
                    )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - supervisor keeps exits alive
                logger.exception("Upper-limit control-loop iteration failed")
                await asyncio.to_thread(self.deadman.ping, healthy=False)
            await asyncio.sleep(0.2)

    async def _websocket_loop(self) -> None:
        """Reconnect real-time protection while preserving the entry-loss latch."""
        backoff = 1.0
        while not self._stop.is_set():
            if not engine_active_at(self.wall_now()):
                await asyncio.sleep(_IDLE_SLEEP_SECONDS)
                continue
            try:
                approval = await asyncio.to_thread(
                    self.adapter.issue_websocket_approval_key
                )
                async with websockets.connect(
                    self.settings.kis_websocket_url,
                    ping_interval=None,
                    close_timeout=2,
                ) as socket:
                    # 구독은 연결에 매인다. 프로세스 단위로 기억하면 재연결 후
                    # "이미 구독함" 으로 건너뛰어 시세가 영영 안 들어오는데
                    # _feed_connected=True 라 REST 폴백까지 멈춘다.
                    self._subscribed.clear()
                    self._feed_connected = True
                    backoff = 1.0
                    await self._call_service(
                        self.service.on_feed_reconnect, priority=_PRIORITY_HIGH
                    )
                    for ticker in self.service.watched_tickers():
                        await self._subscribe(socket, approval, ticker)
                    await self._serve_socket(socket, approval)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - disconnect is a strategy event
                if self._feed_connected:
                    await self._call_service(
                        self.service.on_feed_disconnect,
                        at=self.monotonic(),
                        now_kst=self.wall_now(),
                        priority=_PRIORITY_HIGH,
                    )
                self._feed_connected = False
                logger.exception("Upper-limit WebSocket disconnected")
                # 고정 1초 재시도는 자격증명이 만료되면 인증 API 를 초당 한 번씩 때린다.
                # 2026-07-27 KRX 계정 잠금(하루 158회 재로그인)과 같은 실패 유형이다.
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, _RECONNECT_BACKOFF_MAX)

    async def _serve_socket(self, socket: Any, approval: str) -> None:
        """Multiplex feed reads and subscriptions, dropping a silent connection.

        A half-open socket is worse than a closed one: it keeps
        ``_feed_connected`` true, which suppresses the REST fallback, so price
        protection stops while everything still looks healthy.
        """
        self._last_frame_at = self.monotonic()
        while not self._stop.is_set():
            receive = asyncio.create_task(socket.recv())
            subscribe = asyncio.create_task(self._subscription_queue.get())
            done, pending = await asyncio.wait(
                {receive, subscribe},
                timeout=_FEED_SILENCE_TIMEOUT_SECONDS,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            if feed_is_silent(
                last_frame_at=self._last_frame_at, now=self.monotonic()
            ):
                raise ConnectionError("upper-limit feed silent past timeout")
            if not done:
                continue
            if receive in done:
                self._last_frame_at = self.monotonic()
                raw = str(receive.result())
                if "PINGPONG" in raw and raw.startswith("{"):
                    await socket.send(raw)
                else:
                    await self.dispatch_message_async(raw)
            if subscribe in done:
                await self._subscribe(socket, approval, str(subscribe.result()))

    async def _subscribe(self, socket: Any, approval: str, ticker: str) -> None:
        """Subscribe one ticker exactly once per live process connection."""
        if ticker in self._subscribed:
            return
        await socket.send(subscription_payload(approval, _TRADE_TR_ID, ticker))
        await socket.send(subscription_payload(approval, _QUOTE_TR_ID, ticker))
        self._subscribed.add(ticker)

    def call_threadsafe(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Run one service call from a non-loop thread (FastAPI) safely.

        The admin endpoints run on FastAPI's worker threads. Touching the service
        there would mutate state and commit on the same ORM session the pump is
        using. Everything goes through the same queue instead.

        Args:
            fn: Synchronous service method.
            *args: Positional arguments.
            **kwargs: Keyword arguments.

        Returns:
            Whatever ``fn`` returned.

        Raises:
            RuntimeError: The engine loop is not running.
        """
        loop = self._loop
        if loop is None or not loop.is_running():
            raise RuntimeError("upper-limit runtime loop is not running")
        future = asyncio.run_coroutine_threadsafe(
            self._call_service(fn, *args, priority=_PRIORITY_HIGH, **kwargs), loop
        )
        return future.result(timeout=_ADMIN_CALL_TIMEOUT_SECONDS)

    def emergency_off(self) -> None:
        """Latch the engine off without tearing down exit handling."""
        self.call_threadsafe(self.service.emergency_off)
        logger.warning("Upper-limit V1 emergency OFF — 신규 진입 차단")

    def apply_settings(self, *, mode: str, min_turnover_krw: int) -> None:
        """Apply admin-changed runtime settings to the live service.

        Switching to ``automatic`` re-checks the account-wide live-trading
        switches. Without this an admin API call would place real orders while
        MAPS_LIVE_TRADING_ENABLED says off — the startup gate alone is not
        enough, because mode can change after startup.

        Args:
            mode: New execution mode.
            min_turnover_krw: Liquidity floor; the config rejects anything lower.

        Raises:
            ValueError: Automatic was requested but the safety switches say no.
        """
        if mode == LimitUpMode.AUTOMATIC.value:
            blocked = automatic_mode_blocked_reason(self.settings)
            if blocked is not None:
                raise ValueError(f"automatic mode blocked: {blocked}")
        self.call_threadsafe(self._apply_settings, mode, min_turnover_krw)

    def _apply_settings(self, mode: str, min_turnover_krw: int) -> None:
        """Apply settings on the serialized worker, never a FastAPI thread."""
        self.service.mode = LimitUpMode(mode)
        if self.service.mode is LimitUpMode.AUTOMATIC and self.service.worker:
            # 관리자가 automatic 으로 올리면 청산 실행 능력도 함께 열린다.
            self.service._orders_enabled = True
        self.service.config = LimitUpConfig(
            min_turnover_krw=min_turnover_krw,
            min_execution_strength=self.service.config.min_execution_strength,
            no_fill_timeout_seconds=self.service.config.no_fill_timeout_seconds,
            fill_timeout_seconds=self.service.config.fill_timeout_seconds,
            lock_seconds=self.service.config.lock_seconds,
            hard_stop_drawdown=self.service.config.hard_stop_drawdown,
        )

    async def _run_daily_actions(self, wall: dt.datetime) -> None:
        """Run the 15:18-15:28 overnight review and next-day 08:59:30 exits."""
        clock = wall.time().replace(tzinfo=None)
        stage = eod_stage(clock)
        if stage == "cap":
            for ticker in self.service.locked_tickers():
                key = (wall.date(), ticker)
                if key in self._eod_reviewed:
                    continue
                quote = self._quotes.get(ticker)
                fresh = quote is not None and self.monotonic() - quote.received_at <= 2.0
                await self._call_service(
                    self.service.review_eod,
                    ticker,
                    best_bid_price=quote.best_bid_price if quote else 0,
                    best_bid_qty=quote.best_bid_qty if quote else 0,
                    quote_fresh=fresh,
                    shares_fresh=True,
                    priority=_PRIORITY_HIGH,
                )
                self._eod_reviewed.add(key)
            if wall.date() not in self._overnight_capped:
                await self._call_service(
                self.service.apply_overnight_cap,
                ref_date=wall.date(),
                priority=_PRIORITY_HIGH,
            )
                self._overnight_capped.add(wall.date())
        if stage == "confirm" and wall.date() not in self._overnight_confirmed:
            await self._call_service(
                self.service.confirm_overnight_cap,
                ref_date=wall.date(),
                priority=_PRIORITY_HIGH,
            )
            self._overnight_confirmed.add(wall.date())
        if stage == "force" and wall.date() not in self._overnight_forced:
            await self._call_service(
                self.service.force_overnight_cap,
                ref_date=wall.date(),
                priority=_PRIORITY_HIGH,
            )
            self._overnight_forced.add(wall.date())
        # 08:59:30 은 하한선이다. 30초짜리 창으로 두면 09:00 직후 재시작한 프로세스가
        # 전일 오버나이트 보유를 그대로 들고 하루를 보낸다.
        # before= 가 없으면 15:18 에 막 넘긴 당일 세션을 같은 패스가 곧바로 팔아버린다.
        if clock >= dt.time(8, 59, 30):
            for ticker in self.service.overnight_tickers(before=wall.date()):
                key = (wall.date(), ticker)
                if key in self._opening_submitted:
                    continue
                await self._call_service(
                    self.service.sell_next_open, ticker, priority=_PRIORITY_HIGH
                )
                self._opening_submitted.add(key)
