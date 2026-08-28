"""KIS real-time lifecycle orchestration for the upper-limit V1 engine."""

from __future__ import annotations

import asyncio
import datetime as dt
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
from maps.limit_up.domain import LimitUpState
from maps.limit_up.feed import FeedQuote, FeedTrade, RestFallbackLimiter, parse_kis_ws_message
from maps.limit_up.service import Candidate, LimitUpService


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
        self._opening_submitted: set[tuple[dt.date, str]] = set()
        self._fallback_limiter = RestFallbackLimiter(min_interval_seconds=0.5)

    async def start(self) -> None:
        """Recover broker truth before starting all background loops."""
        now = self.wall_now()
        self.service.recover(
            ref_date=now.date(), now_monotonic=self.monotonic(), now_kst=now
        )
        self._tasks = [
            asyncio.create_task(self._control_loop(), name="limit-up-control"),
            asyncio.create_task(self._websocket_loop(), name="limit-up-websocket"),
        ]

    async def stop(self) -> None:
        """Stop runtime loops while leaving persisted broker state recoverable."""
        self._stop.set()
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self.db.close()

    async def scan_once(self) -> int:
        """Discover broker candidates and subscribe newly accepted common shares."""
        now = self.wall_now()
        if not dt.time(9, 10) <= now.time().replace(tzinfo=None) <= dt.time(14, 30):
            return 0
        rows = await asyncio.to_thread(self.adapter.get_limit_up_candidates)
        accepted = 0
        for row in rows:
            ticker = str(row["ticker"])
            security = (
                self.db.query(SecurityMetadata)
                .filter(SecurityMetadata.ticker == ticker)
                .one_or_none()
            )
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
            if self.service.watch_candidate(candidate, now_kst=now):
                accepted += 1
                await self._subscription_queue.put(ticker)
        return accepted

    def dispatch_message(self, raw: str, *, received_at: float | None = None) -> int:
        """Normalize one WebSocket frame and dispatch it to the shared FSM."""
        at = self.monotonic() if received_at is None else received_at
        events = parse_kis_ws_message(raw, received_at=at)
        now = self.wall_now()
        for event in events:
            if isinstance(event, FeedTrade):
                self.service.on_trade(event, now_kst=now)
            else:
                self._quotes[event.ticker] = event
                self.service.on_quote(event, now_kst=now)
        return len(events)

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
            self.service.on_fallback_price(
                ticker,
                price=int(price),
                at=self.monotonic(),
                now_kst=wall,
            )

    async def _control_loop(self) -> None:
        """Run low-rate scanning, timers, index guard, EOD, and deadman work."""
        while not self._stop.is_set():
            now_mono = self.monotonic()
            wall = self.wall_now()
            try:
                self.service.tick(now_monotonic=now_mono, now_kst=wall)
                if now_mono - self._last_scan_at >= 5.0:
                    self._last_scan_at = now_mono
                    await self.scan_once()
                if now_mono - self._last_index_at >= 1.0:
                    self._last_index_at = now_mono
                    value = await asyncio.to_thread(self.adapter.get_kosdaq_index)
                    self.service.on_kosdaq(value=value, at=now_mono)
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
        while not self._stop.is_set():
            try:
                approval = await asyncio.to_thread(
                    self.adapter.issue_websocket_approval_key
                )
                async with websockets.connect(
                    self.settings.kis_websocket_url,
                    ping_interval=None,
                    close_timeout=2,
                ) as socket:
                    self._feed_connected = True
                    for ticker in self.service.watched_tickers():
                        await self._subscribe(socket, approval, ticker)
                    await self._serve_socket(socket, approval)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - disconnect is a strategy event
                if self._feed_connected:
                    self.service.on_feed_disconnect(
                        at=self.monotonic(), now_kst=self.wall_now()
                    )
                self._feed_connected = False
                logger.exception("Upper-limit WebSocket disconnected")
                await asyncio.sleep(1.0)

    async def _serve_socket(self, socket: Any, approval: str) -> None:
        """Multiplex feed reads and new ticker subscriptions on one connection."""
        while not self._stop.is_set():
            receive = asyncio.create_task(socket.recv())
            subscribe = asyncio.create_task(self._subscription_queue.get())
            done, pending = await asyncio.wait(
                {receive, subscribe}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            if receive in done:
                raw = str(receive.result())
                if "PINGPONG" in raw and raw.startswith("{"):
                    await socket.send(raw)
                else:
                    self.dispatch_message(raw)
            if subscribe in done:
                await self._subscribe(socket, approval, str(subscribe.result()))

    async def _subscribe(self, socket: Any, approval: str, ticker: str) -> None:
        """Subscribe one ticker exactly once per live process connection."""
        if ticker in self._subscribed:
            return
        await socket.send(subscription_payload(approval, _TRADE_TR_ID, ticker))
        await socket.send(subscription_payload(approval, _QUOTE_TR_ID, ticker))
        self._subscribed.add(ticker)

    async def _run_daily_actions(self, wall: dt.datetime) -> None:
        """Run strict 15:18 review and next-day 08:59:30 auction exits."""
        clock = wall.time().replace(tzinfo=None)
        if dt.time(15, 18) <= clock < dt.time(15, 20):
            for ticker in self.service.locked_tickers():
                key = (wall.date(), ticker)
                if key in self._eod_reviewed:
                    continue
                quote = self._quotes.get(ticker)
                fresh = quote is not None and self.monotonic() - quote.received_at <= 2.0
                self.service.review_eod(
                    ticker,
                    best_bid_price=quote.best_bid_price if quote else 0,
                    best_bid_qty=quote.best_bid_qty if quote else 0,
                    quote_fresh=fresh,
                    shares_fresh=True,
                )
                self._eod_reviewed.add(key)
        if dt.time(8, 59, 30) <= clock < dt.time(9, 0):
            for ticker in self.service.overnight_tickers():
                key = (wall.date(), ticker)
                if key in self._opening_submitted:
                    continue
                self.service.sell_next_open(ticker)
                self._opening_submitted.add(key)
