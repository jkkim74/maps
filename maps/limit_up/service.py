"""Application service that binds V1 market events, state, and broker commands."""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from enum import Enum

from maps.common.models import LimitUpSession
from maps.common.settings import MapsSettings, get_settings
from maps.limit_up.domain import (
    CommandKind,
    DailyGuard,
    LimitUpConfig,
    LimitUpMachine,
    LimitUpState,
    MachineCommand,
    QuoteEvent,
    TradeEvent,
    build_grid,
    eod_hold_allowed,
    overnight_allowance,
    overnight_budget,
    trigger_price,
)
from maps.limit_up import notify
from maps.limit_up.feed import FeedQuote, FeedTrade, TapeBuffer
from maps.limit_up.repository import LimitUpRepository
from maps.limit_up.worker import CancelBuysResult, LimitUpCommandWorker


logger = logging.getLogger(__name__)
_UTC = dt.timezone.utc
# 연결이 살아나면 풀려야 하는 래치 — DB 에 남기면 재시작이 되살린다.
_TRANSIENT_LATCHES = frozenset({"feed_disconnected"})


def automatic_mode_blocked_reason(settings: "MapsSettings") -> str | None:
    """Return why automatic order placement is not allowed, or ``None`` if it is.

    V1 orders never went through the scheduler's ``order_cycle``, which is the
    only place ``MAPS_LIVE_TRADING_ENABLED`` was ever enforced (scheduler.py's
    ``if live_enabled and not dry_run``). Without this check the engine would
    place real orders while the account-wide live-trading switch says off — and
    ``_order_log_mode()`` would even record them as ``mock``, because it reads
    that same switch.

    Args:
        settings: Resolved application settings.

    Returns:
        A short machine-readable reason, or ``None`` when automatic is allowed.
    """
    if not settings.maps_live_trading_enabled:
        return "live_trading_disabled"
    if settings.maps_broker_mode != "kis":
        return "broker_not_kis"
    if settings.kis_real_trading and not settings.maps_confirm_real_trading:
        return "real_trading_unconfirmed"
    return None


class LimitUpMode(str, Enum):
    """Upper-limit V1 execution modes."""

    OFF = "off"
    RECOMMEND_ONLY = "recommend_only"
    AUTOMATIC = "automatic"


@dataclass(frozen=True)
class Candidate:
    """Scanner result required to start one V1 watch session."""

    ticker: str
    market: str
    upper_limit_price: int
    total_listed_shares: int
    current_price: int
    change_rate: float
    eligible: bool = True


class LimitUpService:
    """Single-process coordinator for all upper-limit V1 ticker sessions."""

    def __init__(
        self,
        *,
        mode: LimitUpMode,
        config: LimitUpConfig,
        repository: LimitUpRepository,
        worker: LimitUpCommandWorker | None,
    ) -> None:
        """Create a fresh service; call recover before accepting live events."""
        if mode is LimitUpMode.AUTOMATIC and worker is None:
            raise ValueError("automatic mode requires a command worker")
        self.mode = mode
        self.config = config
        self.repository = repository
        self.worker = worker
        self.guard = DailyGuard()
        self.tape = TapeBuffer(window_seconds=60.0)
        self._machines: dict[str, LimitUpMachine] = {}
        self._sessions: dict[str, LimitUpSession] = {}
        self._candidates: dict[str, Candidate] = {}
        self._grids: dict[str, tuple] = {}
        self._virtual_filled_legs: dict[str, set[str]] = {}
        self._last_prices: dict[str, int] = {}
        self._last_reconcile_at: dict[str, float] = {}
        self.manual_lock = False
        self.unknown_positions: list[str] = []
        # 주문을 **낼 수 있는가**(실행 능력)와 새로 **진입할 것인가**(정책)는 다른 질문이다.
        # 이 둘을 mode 하나로 판단하면 비상정지가 청산까지 막아, 실제 주식은 남은 채
        # 시스템만 청산 완료로 판단하는 최악의 상태가 된다.
        self._orders_enabled = mode is LimitUpMode.AUTOMATIC and worker is not None
        # 마지막으로 관측한 단조 시각. 늦은 체결을 되살릴 때 시간 원점으로 쓴다.
        self.monotonic_hint = 0.0

    def watch_candidate(
        self, candidate: Candidate, *, now_kst: dt.datetime
    ) -> str | None:
        """Start watching an eligible +25% common-share candidate in entry hours.

        Returns ``None`` when the watch started, otherwise a short reason the
        scanner can count — the same polarity as
        :meth:`automatic_mode_blocked_reason`. A bare ``False`` made every gate
        look identical in the logs, so a rejected scan and an empty one could
        not be told apart after the fact.
        """
        if self.mode is LimitUpMode.OFF:
            return "mode_off"
        if self.manual_lock:
            return "manual_lock"
        if not candidate.eligible:
            return "ineligible"
        if candidate.market not in {"KOSPI", "KOSDAQ"}:
            return "market"
        if candidate.change_rate < 25.0:
            return "below_trigger"
        if not dt.time(9, 10) <= now_kst.timetz().replace(tzinfo=None) <= dt.time(14, 30):
            return "outside_hours"
        if candidate.ticker in self._machines:
            return "already_watching"
        session = self.repository.create_or_get_session(
            ref_date=now_kst.date(),
            ticker=candidate.ticker,
            market=candidate.market,
            upper_limit_price=candidate.upper_limit_price,
            trigger_price=trigger_price(candidate.upper_limit_price),
            total_listed_shares=candidate.total_listed_shares,
            execution_mode=self.mode.value,
        )
        if session.state != LimitUpState.WATCHING.value:
            return "session_not_watching"
        machine = LimitUpMachine(
            candidate.ticker,
            upper_limit_price=candidate.upper_limit_price,
            config=self.config,
        )
        self._sessions[candidate.ticker] = session
        self._machines[candidate.ticker] = machine
        self._candidates[candidate.ticker] = candidate
        self._last_prices[candidate.ticker] = candidate.current_price
        self.repository.db.commit()
        notify.watch_started(session)
        return None

    def on_trade(self, trade: FeedTrade, *, now_kst: dt.datetime) -> None:
        """Process one normalized trade without database I/O in the tape callback path."""
        machine = self._machines.get(trade.ticker)
        if machine is None:
            return
        self._last_prices[trade.ticker] = trade.price
        self.tape.append(
            trade.ticker,
            at=trade.received_at,
            payload={
                "kind": "trade",
                "price": trade.price,
                "turnover": trade.cumulative_turnover_krw,
                "strength": trade.execution_strength,
                "buy": trade.buy_initiated,
            },
        )
        if self._is_virtual(trade.ticker):
            self._apply_virtual_fills(trade.ticker, trade.price, trade.received_at, now_kst)
        commands = machine.on_trade(
            TradeEvent(
                at=trade.received_at,
                price=trade.price,
                buy_initiated=trade.buy_initiated,
                cumulative_turnover_krw=trade.cumulative_turnover_krw,
                execution_strength=trade.execution_strength,
            )
        )
        self._handle_commands(trade.ticker, commands, now_kst=now_kst, trade=trade)

    def on_quote(self, quote: FeedQuote, *, now_kst: dt.datetime) -> None:
        """Process one best-level quote and track the continuous lock buffer."""
        machine = self._machines.get(quote.ticker)
        if machine is None:
            return
        self.tape.append(
            quote.ticker,
            at=quote.received_at,
            payload={
                "kind": "quote",
                "ask_price": quote.best_ask_price,
                "ask_qty": quote.best_ask_qty,
                "bid_price": quote.best_bid_price,
                "bid_qty": quote.best_bid_qty,
            },
        )
        price = self._last_prices.get(quote.ticker, 0)
        if quote.best_ask_qty == 0 and quote.best_ask_price > 0:
            price = quote.best_ask_price
            self._last_prices[quote.ticker] = price
        if price <= 0:
            # 가격을 모르는 상태다. 0 을 그대로 흘리면 `0 < 상한가×0.95` 가 참이 되어
            # 멀쩡한 보유가 가짜 하드스톱(`hard_stop_price` 비교)으로 전량 청산된다. REST 경로가 `if price > 0`
            # 가드를 두는 것과 같은 이유다 — 모르는 것은 폭락이 아니다.
            return
        commands = machine.on_quote(
            QuoteEvent(
                at=quote.received_at,
                price=price,
                best_ask_price=quote.best_ask_price,
                best_ask_qty=quote.best_ask_qty,
            )
        )
        self._handle_commands(quote.ticker, commands, now_kst=now_kst)

    def tick(self, *, now_monotonic: float, now_kst: dt.datetime | None = None) -> None:
        """Apply all session timers and reconcile automatic broker fills."""
        wall = now_kst or dt.datetime.now(dt.timezone(dt.timedelta(hours=9)))
        self.monotonic_hint = now_monotonic
        for ticker, machine in list(self._machines.items()):
            last_reconcile = self._last_reconcile_at.get(ticker, float("-inf"))
            if self.can_place_exit_for(ticker) and now_monotonic - last_reconcile >= 1.0:
                if machine.state in {LimitUpState.NET_OPEN, LimitUpState.FILLED_WAIT_LOCK}:
                    self._last_reconcile_at[ticker] = now_monotonic
                    result = self.worker.reconcile(self._sessions[ticker])
                    if result.owned_quantity > machine.filled_quantity:
                        self._record_fill(ticker, result.owned_quantity, now_monotonic, wall)
                elif machine.state is LimitUpState.RECONCILING:
                    # 청산은 제출 시점에 체결되지 않는 일이 흔하다. 여기서 계속 확인하지
                    # 않으면 세션이 RECONCILING 에 영원히 남아 슬롯을 잡고, 늦게 잡힌
                    # 청산 손익이 NULL 로 남아 일일 중단선에 반영되지 않는다.
                    self._last_reconcile_at[ticker] = now_monotonic
                    session = self._sessions[ticker]
                    # cancel_pending_buys 가 내부에서 reconcile 하므로 앞서 따로 하면
                    # 첫 결과를 버리고 I/O 절반을 낭비한다. 청산 후에는 레그가 종결
                    # 상태라 취소 루프는 아무것도 안 보내고 reconcile 만 남는다.
                    cancel = self._cancel_buys_safely(session)
                    if cancel is None:
                        continue
                    if not cancel.is_clear:
                        self.manual_lock = True
                        continue
                    result = cancel.reconciliation
                    if result.owned_quantity == 0:
                        machine.state = LimitUpState.CLOSED
                        machine.filled_quantity = 0
                        session.state = LimitUpState.CLOSED.value
                        self.repository.db.commit()
                    elif not result.open_exit_order_ids:
                        # 손절 제출이 예외로 실패하면 상태만 RECONCILING 이 되고 이후
                        # 손절 이벤트는 중복 방지로 무시된다. 열린 주문이 하나도 없는데
                        # 보유가 남아 있다는 건 매도가 나가지 않았다는 뜻이다.
                        self._retry_stuck_exit(ticker, result.owned_quantity)
            commands = machine.on_timer(now_monotonic)
            self._handle_commands(ticker, commands, now_kst=wall)

    def on_kosdaq(
        self, *, value: float, at: float, now_kst: dt.datetime | None = None
    ) -> None:
        """Latch panic drawdown and cancel every still-pending V1 buy grid.

        The high is persisted on every observation, not just on a latch: a
        restart that rebuilds it from the post-restart tape would start from an
        already-depressed price and never latch at all.
        """
        now = now_kst or dt.datetime.now(dt.timezone(dt.timedelta(hours=9)))
        # 날짜를 먼저 맞추지 않으면 기동 직후 관측이 ref_date=None 가드에 쌓이고,
        # 첫 진입 심사가 가드를 교체하는 순간 방금 걸린 래치가 사라진다.
        self._ensure_guard_for(now.date())
        latched = self.guard.observe_kosdaq(value)
        self._persist_guard()
        if not latched:
            return
        high = self.guard.kosdaq_high or value
        logger.warning(
            "상한가 진입 차단 — kosdaq_drawdown 래치 (고점 %.2f → 현재 %.2f, %.2f%%)",
            high,
            value,
            (value - high) / high * 100,
        )
        for ticker, machine in list(self._machines.items()):
            commands = machine.on_market_halt(at=at)
            self._handle_commands(ticker, commands, now_kst=now)

    def on_feed_reconnect(self) -> None:
        """Clear the feed-loss latch once real-time data is flowing again.

        Nothing released this latch, and 4차에서 영속화까지 해서 1초짜리 끊김이
        재시작을 넘어 그날 전체 진입을 막았다. The engine would run all day,
        scanning and subscribing, placing nothing.
        """
        if "feed_disconnected" not in self.guard.halted_reasons:
            return
        self.guard.halted_reasons.discard("feed_disconnected")
        self._persist_guard()
        logger.warning("상한가 피드 복구 — feed_disconnected 래치 해제")

    def on_feed_disconnect(self, *, at: float, now_kst: dt.datetime) -> None:
        """Fail-close new entries and pull only unfilled nets on feed loss."""
        self._ensure_guard_for(now_kst.date())
        self.guard.halted_reasons.add("feed_disconnected")
        self._persist_guard()
        for ticker, machine in list(self._machines.items()):
            commands = machine.on_market_halt(at=at)
            self._handle_commands(ticker, commands, now_kst=now_kst)

    def on_fallback_price(
        self,
        ticker: str,
        *,
        price: int,
        at: float,
        now_kst: dt.datetime,
    ) -> None:
        """Apply one REST price to held-position protection without entry gates."""
        machine = self._machines.get(ticker)
        if machine is None:
            return
        self._last_prices[ticker] = price
        commands = machine.on_trade(
            TradeEvent(
                at=at,
                price=price,
                buy_initiated=False,
                cumulative_turnover_krw=0,
                execution_strength=0.0,
            )
        )
        self._handle_commands(ticker, commands, now_kst=now_kst)

    def review_eod(
        self,
        ticker: str,
        *,
        best_bid_price: int,
        best_bid_qty: int,
        quote_fresh: bool,
        shares_fresh: bool,
    ) -> str:
        """Apply the strict 15:18/15:18:30 overnight review to one locked session."""
        machine = self._machines[ticker]
        session = self._sessions[ticker]
        candidate = self._candidates[ticker]
        allowed = eod_hold_allowed(
            upper_limit_price=candidate.upper_limit_price,
            best_bid_price=best_bid_price,
            best_bid_qty=best_bid_qty,
            total_listed_shares=candidate.total_listed_shares,
            quote_fresh=quote_fresh,
            shares_fresh=shares_fresh,
        )
        if allowed:
            machine.state = LimitUpState.OVERNIGHT
            session.eod_decision = "hold"
            self.repository.transition(
                session, state=LimitUpState.OVERNIGHT, action="eod_hold"
            )
            self._dump_tape(ticker, "EOD_DECISION")
            self.repository.db.commit()
            return "hold"
        session.eod_decision = "sell"
        machine.state = LimitUpState.RECONCILING
        self.repository.transition(
            session, state=LimitUpState.RECONCILING, action="eod_sell"
        )
        self._dump_tape(ticker, "EOD_DECISION")
        if self.can_place_exit_for(ticker):
            self.worker.sell_actual_position(
                session, reason="eod_review_fail", owned_quantity=machine.filled_quantity
            )
        elif self._is_virtual(ticker):
            self._close_virtual(ticker, "eod_review_fail", session.ref_date)
        elif not self._strand_unprotected(ticker, "eod_review_fail"):
            machine.state = LimitUpState.CLOSED
            session.state = LimitUpState.CLOSED.value
            session.end_reason = "eod_review_fail"
        self.repository.db.commit()
        return "sell"

    def emergency_off(self) -> None:
        """Latch every entry path off immediately.

        Entries only. ``_orders_enabled`` is deliberately left alone: a kill
        switch that also blocked selling would strand whatever is already held
        and — worse — let the system mark those sessions closed without an order
        ever reaching the broker.
        """
        self.mode = LimitUpMode.OFF
        self._ensure_guard_for(
            dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).date()
        )
        self.guard.halted_reasons.add("emergency_off")
        self._persist_guard()

    def _strand_unprotected(self, ticker: str, reason: str) -> bool:
        """Refuse to close a session that still holds shares we cannot sell.

        Marking it CLOSED without an order is the worst outcome available: the
        row drops out of ``recover()``, the after-hours watch and the forced
        liquidation all at once, so nothing ever looks at those shares again.
        Lock the engine and shout instead.

        Args:
            ticker: Session ticker.
            reason: What tried to close it.

        Returns:
            ``True`` when the session was stranded and must not be closed.
        """
        machine = self._machines[ticker]
        if machine.filled_quantity <= 0:
            return False
        self.manual_lock = True
        if ticker not in self.unknown_positions:
            self.unknown_positions = sorted({*self.unknown_positions, ticker})
        logger.error(
            "주문을 낼 수 없는데 실보유가 있다 [%s] qty=%s reason=%s — "
            "세션을 닫지 않고 수동 잠금한다",
            ticker, machine.filled_quantity, reason,
        )
        return True

    def set_mode(self, mode: LimitUpMode) -> None:
        """Change the entry policy, opening order capability when it rises.

        The rule lives here, not in the runtime: splitting it across two modules
        is how the two halves drift apart. Capability is never lowered — an
        existing position must stay sellable after a downgrade, for the same
        reason ``emergency_off()`` leaves it alone.

        Args:
            mode: New execution mode.
        """
        self.mode = mode
        if mode is LimitUpMode.AUTOMATIC and self.worker is not None:
            self._orders_enabled = True

    def _is_virtual(self, ticker: str) -> bool:
        """Return whether this session's fills came from simulation, not a broker.

        Recommendation mode fills legs on paper. If order capability is later
        raised — or simply never lowered after a downgrade — those imaginary
        shares would be "sold" for real, hitting whatever the account actually
        holds in that ticker.
        """
        session = self._sessions.get(ticker)
        return session is not None and session.execution_mode == LimitUpMode.RECOMMEND_ONLY.value

    def can_place_exit_for(self, ticker: str) -> bool:
        """Return whether a real exit order may be sent for one session."""
        session = self._sessions.get(ticker)
        return (
            self.exits_are_live()
            and session is not None
            and session.execution_mode == LimitUpMode.AUTOMATIC.value
        )

    def exits_are_live(self) -> bool:
        """Return whether protective orders can still reach the broker.

        Not ``mode``: entry policy may be off (emergency stop, KOSDAQ latch)
        while a real position still has to be sold.
        """
        return self._orders_enabled and self.worker is not None

    def carried_tickers(self) -> list[str]:
        """Return sessions still competing for the shared overnight budget."""
        return sorted(
            ticker
            for ticker, machine in list(self._machines.items())
            if machine.state in {LimitUpState.OVERNIGHT, LimitUpState.EOD_TRIM}
        )

    def overnight_allowances(self, ref_date: dt.date) -> dict[str, int]:
        """Return each carried session's share cap under today's risk budget.

        Recomputed at every checkpoint rather than frozen at 15:18, so a session
        closing in between re-splits the budget across whoever is left.
        """
        budget = overnight_budget(self.repository.realized_pnl_total(ref_date))
        allowances: dict[str, int] = {}
        tickers = self.carried_tickers()
        for execution_mode in (
            LimitUpMode.AUTOMATIC.value,
            LimitUpMode.RECOMMEND_ONLY.value,
        ):
            group = [
                ticker
                for ticker in tickers
                if self._sessions[ticker].execution_mode == execution_mode
            ]
            for ticker in group:
                allowances[ticker] = overnight_allowance(
                    budget_krw=budget,
                    session_count=len(group),
                    upper_limit_price=self._machines[ticker].upper_limit_price,
                )
        return allowances

    def apply_overnight_cap(self, *, ref_date: dt.date) -> dict[str, int]:
        """15:18 — sell the excess over each carried session's budget share.

        Trimming here rather than at entry keeps intraday sizing intact: most
        sessions close the same day and never need the overnight cap at all.

        Args:
            ref_date: KST trading date whose realized P/L funds the budget.

        Returns:
            Excess share count submitted per ticker, empty when nothing is over.
        """
        submitted: dict[str, int] = {}
        for ticker, allowed in self.overnight_allowances(ref_date).items():
            machine = self._machines[ticker]
            session = self._sessions[ticker]
            if machine.state is LimitUpState.EOD_TRIM:
                # 이미 트림이 나가 있다. 다시 전이하면 state_version 이 올라가 멱등 키가
                # 새로 발급되고 **같은 주문이 또 나간다**(중복 가드에 걸려 그 회차의
                # 15:25 확인·15:28 강제청산까지 함께 죽는다). 체결 여부는 confirm 이 본다.
                continue
            session = self._sessions[ticker]
            if self.can_place_exit_for(ticker):
                # tick() only reconciles NET_OPEN/FILLED_WAIT_LOCK, so a locked
                # session's cached quantity can lag the actual holding. Sizing the
                # trim off a stale number would under-trim and breach the cap.
                machine.filled_quantity = self.worker.reconcile(session).owned_quantity
            excess = machine.filled_quantity - allowed
            if excess <= 0:
                continue
            machine.state = LimitUpState.EOD_TRIM
            self.repository.transition(
                session,
                state=LimitUpState.EOD_TRIM,
                action="overnight_trim",
                payload={"allowed": allowed, "excess": excess},
            )
            self.repository.db.commit()
            if self.can_place_exit_for(ticker):
                self.worker.sell_overnight_excess(
                    session, quantity=excess, price=machine.upper_limit_price
                )
            elif self._is_virtual(ticker):
                self.repository.add_exit_quantity(
                    session, ref_date=ref_date, quantity=excess
                )
                machine.filled_quantity = self.repository.remaining_quantity(session)
            else:
                self._strand_unprotected(ticker, "overnight_trim")
            submitted[ticker] = excess
        self.repository.db.commit()
        return submitted

    def confirm_overnight_cap(self, *, ref_date: dt.date) -> list[str]:
        """15:25 — return sessions whose trim filled to the overnight carry.

        Args:
            ref_date: KST trading date used to re-split the budget.

        Returns:
            Tickers restored to ``OVERNIGHT``.
        """
        allowances = self.overnight_allowances(ref_date)
        restored: list[str] = []
        for ticker, allowed in allowances.items():
            machine = self._machines[ticker]
            if machine.state is not LimitUpState.EOD_TRIM:
                continue
            session = self._sessions[ticker]
            if self.can_place_exit_for(ticker):
                machine.filled_quantity = self.worker.reconcile(session).owned_quantity
            if machine.filled_quantity > allowed:
                continue
            machine.state = LimitUpState.OVERNIGHT
            self.repository.transition(
                session, state=LimitUpState.OVERNIGHT, action="overnight_trim_filled"
            )
            restored.append(ticker)
        self.repository.db.commit()
        return restored

    def force_overnight_cap(self, *, ref_date: dt.date) -> list[str]:
        """15:28 — give up the carry for any session still over its budget.

        Failing closed here is what makes the absolute cap a guarantee rather
        than a hope: if the trim cannot fill, nothing goes overnight.

        Args:
            ref_date: KST trading date used to re-split the budget.

        Returns:
            Tickers liquidated in full.
        """
        liquidated: list[str] = []
        # EOD_TRIM(캡 미체결)뿐 아니라 LOCKED 도 대상이다. 장애·재시작으로 15:18 창을
        # 놓치면 심사 자체를 못 받은 LOCKED 세션이 상한 적용 없이 익일로 넘어간다.
        # 심사받지 않은 포지션은 오버나이트 자격이 없다 — fail-closed.
        stranded = {LimitUpState.EOD_TRIM, LimitUpState.LOCKED}
        candidates = sorted(
            ticker
            for ticker, machine in list(self._machines.items())
            if machine.state in stranded and machine.filled_quantity > 0
        )
        for ticker in candidates:
            machine = self._machines[ticker]
            reason = (
                "overnight_cap_unfilled"
                if machine.state is LimitUpState.EOD_TRIM
                else "eod_review_missed"
            )
            session = self._sessions[ticker]
            machine.state = LimitUpState.RECONCILING
            self.repository.transition(
                session, state=LimitUpState.RECONCILING, action=reason
            )
            if self.can_place_exit_for(ticker):
                if not self.worker.cancel_open_exits(session).is_clear:
                    logger.error(
                        "강제 청산 보류 [%s] — 취소되지 않은 매도가 남아 있다", ticker
                    )
                    continue
                result = self.worker.sell_actual_position(
                    session, reason=reason, owned_quantity=machine.filled_quantity
                )
                if result.owned_quantity == 0:
                    machine.state = LimitUpState.CLOSED
                    machine.filled_quantity = 0
            elif self._is_virtual(ticker):
                self._close_virtual(ticker, reason, ref_date)
            elif not self._strand_unprotected(ticker, reason):
                machine.state = LimitUpState.CLOSED
            session.state = machine.state.value
            session.end_reason = reason
            liquidated.append(ticker)
        self.repository.db.commit()
        return liquidated

    def recover(
        self,
        *,
        ref_date: dt.date,
        now_monotonic: float,
        now_kst: dt.datetime | None = None,
    ) -> None:
        """Pause entries and rebuild known sessions from broker-authoritative holdings."""
        wall = now_kst or dt.datetime.now(dt.timezone(dt.timedelta(hours=9)))
        # 기동일 세션만 복구하면, 익일 아침에 재시작했을 때 전일 오버나이트 보유가
        # unknown_position 으로 밀려 08:59:30 청산 창(30초)에서도 빠진다. 미종료 세션은
        # 날짜와 무관하게 전부 되살린다 — 방치된 포지션을 찾는 것이 이 함수의 목적이다.
        rows = (
            self.repository.db.query(LimitUpSession)
            .filter(LimitUpSession.ref_date <= ref_date)
            .filter(LimitUpSession.state != LimitUpState.CLOSED.value)
            .order_by(LimitUpSession.ref_date)
            .all()
        )
        known = {
            row.ticker
            for row in rows
            if row.execution_mode == LimitUpMode.AUTOMATIC.value
        }
        actual = self.worker.broker.get_positions() if self.worker is not None else {}
        # 공유 계좌라 잔고에는 타 전략·수동 보유가 섞여 있다. order_log 의 limit_up
        # 레인 흔적이 있는 티커만 우리 고아일 수 있다 — 전부 잠그면 recommend_only 는
        # known 이 공집합이라 계좌에 뭐라도 있는 순간 무조건 잠긴다(2026-08-31 사고).
        broker_mode = get_settings().maps_broker_mode
        self.unknown_positions = sorted(
            ticker
            for ticker, quantity in actual.items()
            if quantity > 0
            and ticker not in known
            and self.repository.has_unresolved_limit_up_trace(ticker, broker=broker_mode)
        )
        if self.unknown_positions:
            self.manual_lock = True
            logger.error(
                "고아 limit_up 보유로 수동 잠금: %s — 계좌 정리 후 재기동해야 풀린다",
                ", ".join(self.unknown_positions),
            )
        if self.worker is not None and any(
            row.execution_mode == LimitUpMode.AUTOMATIC.value for row in rows
        ):
            self._orders_enabled = True
        for row in rows:
            machine = LimitUpMachine(
                row.ticker,
                upper_limit_price=row.upper_limit_price,
                config=self.config,
            )
            machine.state = LimitUpState(row.state)
            # 계좌 보유를 그대로 세션 보유로 삼으면 공유 계좌에서 남의 물량을 V1 이
            # 산 것으로 장부에 올린다. 세션 자신의 레그 체결과의 min 이 소유분이다.
            position_qty = actual.get(row.ticker, 0)
            if row.execution_mode == LimitUpMode.AUTOMATIC.value and self.worker is not None:
                reconciled = self.worker.reconcile(row)
                position_qty = reconciled.position_quantity
                machine.filled_quantity = reconciled.owned_quantity
            else:
                machine.filled_quantity = self.repository.remaining_quantity(row)
            exposed = row.state != LimitUpState.WATCHING.value or self.repository.bought_quantity(row) > 0
            if (row.execution_mode == "unknown" and exposed) or (
                row.execution_mode == LimitUpMode.AUTOMATIC.value and self.worker is None
            ):
                # 출처를 모르는 **보유**만 잠근다. 감시만 하다 끝난 행까지 잠그면
                # 재시작마다 영구 수동잠금이 걸린다.
                self.manual_lock = True
                self.unknown_positions = sorted({*self.unknown_positions, row.ticker})
            if row.first_fill_at is not None:
                first_fill = row.first_fill_at.replace(tzinfo=_UTC)
                elapsed = max(0.0, (wall.astimezone(_UTC) - first_fill).total_seconds())
                machine.first_fill_at = now_monotonic - elapsed
            if row.net_fired_at is not None:
                # 이걸 복원하지 않으면 재시작 후 미체결 매수가 180초 타임아웃을 영영
                # 못 만나고 살아남는다(on_timer 가 net_fired_at 을 요구한다).
                fired = row.net_fired_at.replace(tzinfo=_UTC)
                elapsed = max(0.0, (wall.astimezone(_UTC) - fired).total_seconds())
                machine.net_fired_at = now_monotonic - elapsed
            # 가격을 복원하지 않으면 재연결 후 첫 호가가 0 원으로 평가된다.
            # 호가창 스냅샷은 보통 체결보다 먼저 도착하므로 거의 매번 그렇게 된다.
            held = (
                self.worker.broker.get_position(row.ticker)
                if self.worker is not None
                and row.execution_mode == LimitUpMode.AUTOMATIC.value
                else None
            )
            self._last_prices[row.ticker] = int(
                (held.current_price or held.avg_price) if held else row.upper_limit_price
            )
            self._sessions[row.ticker] = row
            self._machines[row.ticker] = machine
            self._candidates[row.ticker] = Candidate(
                ticker=row.ticker,
                market=row.market,
                upper_limit_price=row.upper_limit_price,
                total_listed_shares=row.total_listed_shares,
                current_price=0,
                change_rate=0.0,
            )
            if (
                row.execution_mode == LimitUpMode.AUTOMATIC.value
                and position_qty == 0
                and row.state in {
                LimitUpState.FILLED_WAIT_LOCK.value,
                LimitUpState.LOCKED.value,
                LimitUpState.OVERNIGHT.value,
                }
            ):
                row.state = LimitUpState.RECONCILING.value
        self.repository.db.commit()
        self._resubmit_interrupted_exits(wall)

    def _retry_stuck_exit(self, ticker: str, owned_quantity: int) -> None:
        """Re-send an exit for a position whose sell never reached the broker.

        A broker error during the protective exit leaves the machine in
        RECONCILING, and the state machine suppresses repeat exits from there —
        so without this the position sits outside every guard until someone
        notices. Only fires when the broker reports no open order at all.

        Args:
            ticker: Session ticker.
            owned_quantity: Broker-confirmed shares owned by this session.
        """
        session = self._sessions[ticker]
        machine = self._machines[ticker]
        logger.error(
            "청산이 나가지 않은 보유 발견 [%s] qty=%s — 재제출한다",
            ticker, owned_quantity,
        )
        if not self.worker.cancel_open_exits(session).is_clear:
            return
        result = self.worker.sell_actual_position(
            session,
            reason=session.end_reason or "stuck_exit_retry",
            owned_quantity=owned_quantity,
        )
        if result.owned_quantity == 0:
            machine.state = LimitUpState.CLOSED
            machine.filled_quantity = 0
            session.state = LimitUpState.CLOSED.value
        self.repository.db.commit()

    def _resubmit_interrupted_exits(self, now_kst: dt.datetime) -> None:
        """Re-issue exits for positions left in RECONCILING by a crash.

        Dying between "decided to sell" and "sent the order" leaves a real
        position whose session says it is exiting. Reconciling alone would just
        keep confirming the holding forever, so the order has to go out again.

        Args:
            now_kst: Wall-clock time used for the transition record.
        """
        for ticker, machine in list(self._machines.items()):
            if machine.state is not LimitUpState.RECONCILING:
                continue
            if machine.filled_quantity <= 0:
                continue
            session = self._sessions[ticker]
            if self._is_virtual(ticker):
                self._close_virtual(
                    ticker,
                    session.end_reason or "recovered_exit",
                    now_kst.date(),
                )
                continue
            if not self.can_place_exit_for(ticker):
                self._strand_unprotected(ticker, session.end_reason or "recovered_exit")
                continue
            if not self.worker.cancel_open_exits(session).is_clear:
                # 취소하지 못한 매도가 살아 있다. 그 위에 전량매도를 얹으면 보유보다
                # 많이 팔거나 잔량 부족으로 거절된다 — 사람이 볼 수 있게 두고 멈춘다.
                logger.error(
                    "청산 재제출 보류 [%s] — 취소되지 않은 매도가 남아 있다", ticker
                )
                continue
            result = self.worker.sell_actual_position(
                session,
                reason=session.end_reason or "recovered_exit",
                owned_quantity=machine.filled_quantity,
            )
            if result.owned_quantity == 0:
                machine.state = LimitUpState.CLOSED
                machine.filled_quantity = 0
                session.state = LimitUpState.CLOSED.value
        self.repository.db.commit()

    def machine(self, ticker: str) -> LimitUpMachine:
        """Return one live state machine for tests and admin diagnostics."""
        return self._machines[ticker]

    def quote_event(self, ticker: str, *, price: int, ask_qty: int, at: float) -> QuoteEvent:
        """Build the exact quote event used by the service state machine."""
        del ticker
        return QuoteEvent(at=at, price=price, best_ask_price=price, best_ask_qty=ask_qty)

    def status(self) -> dict:
        """Return an admin-safe in-memory operational snapshot."""
        return {
            "mode": self.mode.value,
            "attempts": self.guard.attempts,
            "daily_pnl": self.guard.daily_pnl,
            "pattern_failures": self.guard.pattern_failures,
            "entry_halted": bool(self.guard.halted_reasons) or self.manual_lock,
            "halted_reasons": sorted(self.guard.halted_reasons),
            "manual_lock": self.manual_lock,
            "unknown_positions": list(self.unknown_positions),
            "sessions": {
                ticker: {
                    "state": machine.state.value,
                    "filled_quantity": machine.filled_quantity,
                }
                for ticker, machine in list(self._machines.items())
            },
        }

    def watched_tickers(self) -> tuple[str, ...]:
        """Return every persisted live ticker in deterministic order."""
        return tuple(sorted(list(self._machines)))

    def held_tickers(self) -> list[str]:
        """Return tickers whose broker position still needs protection."""
        held_states = {
            LimitUpState.FILLED_WAIT_LOCK,
            LimitUpState.LOCKED,
            LimitUpState.EOD_REVIEW,
            LimitUpState.EOD_TRIM,
            LimitUpState.OVERNIGHT,
            LimitUpState.RECONCILING,
        }
        return sorted(
            ticker
            for ticker, machine in list(self._machines.items())
            if machine.filled_quantity > 0 and machine.state in held_states
        )

    def locked_tickers(self) -> list[str]:
        """Return sessions eligible for the strict 15:18 review."""
        return sorted(
            ticker
            for ticker, machine in list(self._machines.items())
            if machine.state is LimitUpState.LOCKED
        )

    def overnight_tickers(self, *, before: dt.date | None = None) -> list[str]:
        """Return carries due for the next opening-auction exit.

        ``before`` excludes sessions entered on that date, which is what makes
        this the *next* open rather than this one: 15:18 turns a session into
        ``OVERNIGHT`` while the market is still open, and without the date guard
        the same daily-actions pass would sell it minutes later.

        ``AFTER_HOURS_EXIT`` is included: an after-hours escape that never filled
        still holds shares, and leaving it out would strand the position for
        another whole day — the exact risk the escape existed to avoid.

        Args:
            before: Only return sessions whose ``ref_date`` precedes this date.

        Returns:
            Tickers to exit at the opening auction.
        """
        carried = {LimitUpState.OVERNIGHT, LimitUpState.AFTER_HOURS_EXIT}
        return sorted(
            ticker
            for ticker, machine in list(self._machines.items())
            if machine.state in carried
            and (before is None or self._sessions[ticker].ref_date < before)
        )

    def sell_next_open(self, ticker: str) -> None:
        """Submit one persisted market exit for an overnight session."""
        machine = self._machines[ticker]
        session = self._sessions[ticker]
        machine.state = LimitUpState.RECONCILING
        self.repository.transition(
            session,
            state=LimitUpState.RECONCILING,
            action="next_open_sell",
        )
        if self.can_place_exit_for(ticker):
            result = self.worker.sell_actual_position(
                session, reason="next_open", owned_quantity=machine.filled_quantity
            )
            if result.owned_quantity == 0:
                machine.state = LimitUpState.CLOSED
                machine.filled_quantity = 0
                session.state = LimitUpState.CLOSED.value
                session.end_reason = "next_open"
        elif self._is_virtual(ticker):
            self._close_virtual(
                ticker,
                "next_open",
                dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).date(),
            )
        elif not self._strand_unprotected(ticker, "next_open"):
            machine.state = LimitUpState.CLOSED
            session.state = LimitUpState.CLOSED.value
            session.end_reason = "next_open"
        self.repository.db.commit()

    def _close_virtual(self, ticker: str, reason: str, ref_date: dt.date) -> None:
        """Close a paper position in its durable ledger without broker I/O."""
        session = self._sessions[ticker]
        machine = self._machines[ticker]
        self.repository.add_exit_quantity(
            session, ref_date=ref_date, quantity=machine.filled_quantity
        )
        machine.filled_quantity = self.repository.remaining_quantity(session)
        machine.state = LimitUpState.CLOSED
        session.state = LimitUpState.CLOSED.value
        session.end_reason = reason

    def control_lost(self) -> bool:
        """Return whether exposure exists without trustworthy automation control."""
        feed_lost = "feed_disconnected" in self.guard.halted_reasons
        return self.manual_lock or (feed_lost and bool(self.held_tickers()))

    def _cancel_buys_safely(self, session: LimitUpSession) -> CancelBuysResult | None:
        """Cancel buys, latching manual control if broker I/O is unavailable."""
        try:
            return self.worker.cancel_pending_buys(session)
        except Exception:
            self.manual_lock = True
            logger.exception(
                "매수 취소/재조정 실패로 수동 잠금 [%s]", session.ticker
            )
            return None

    def _apply_virtual_fills(
        self, ticker: str, price: int, at: float, now_kst: dt.datetime
    ) -> None:
        """Apply deterministic touch fills only in recommendation mode."""
        machine = self._machines[ticker]
        if machine.state is not LimitUpState.NET_OPEN:
            return
        # 이 경로는 체결 프레임마다 돈다. 레그 두 개의 가격 비교는 메모리로 끝내고,
        # DB 는 플립(레그당 평생 1회)에만 쓴다 — 프레임마다 SELECT/flush 를 하면
        # 펌프 스레드에서 종목당 수천 쿼리가 실주문 세션의 손절 프레임 앞을 막는다.
        session = self._sessions[ticker]
        filled = self._virtual_filled_legs.setdefault(ticker, set())
        flipped = [
            leg for leg in self._grids.get(ticker, ())
            if leg.name not in filled and price <= leg.price
        ]
        if not flipped:
            return
        for leg in flipped:
            filled.add(leg.name)
            row = self.repository.upsert_leg(
                session, name=leg.name, price=leg.price, quantity=leg.quantity,
                status="simulated_filled",
            )
            row.filled_quantity = leg.quantity
            row.avg_fill_price = float(leg.price)
        self.repository.db.flush()
        cumulative = self.repository.remaining_quantity(session)
        if cumulative > machine.filled_quantity:
            self._record_fill(ticker, cumulative, at, now_kst)

    def _record_fill(
        self, ticker: str, quantity: int, at: float, now_kst: dt.datetime
    ) -> None:
        """Record the first broker or virtual fill and force a tape dump."""
        machine = self._machines[ticker]
        first = machine.first_fill_at is None
        machine.on_fill(
            at=at,
            cumulative_quantity=quantity,
            avg_price=self.repository.average_fill_price(self._sessions[ticker]),
        )
        session = self._sessions[ticker]
        if first:
            session.first_fill_at = _utc_naive(now_kst)
            self.repository.transition(
                session,
                state=LimitUpState.FILLED_WAIT_LOCK,
                action="first_fill",
                payload={"quantity": quantity},
            )
            self._dump_tape(ticker, "FIRST_FILL")
            self.repository.db.commit()

    def _handle_commands(
        self,
        ticker: str,
        commands: list[MachineCommand],
        *,
        now_kst: dt.datetime,
        trade: FeedTrade | None = None,
    ) -> None:
        """Persist transitions before dispatching all emitted side effects."""
        for command in commands:
            session = self._sessions[ticker]
            machine = self._machines[ticker]
            if command.kind is CommandKind.FIRE_NET:
                self._refresh_daily_pnl(now_kst.date())
                if not self.guard.can_enter(active_sessions=self._active_count(exclude=ticker)):
                    machine.state = LimitUpState.CLOSED
                    session.state = LimitUpState.CLOSED.value
                    session.end_reason = "daily_guard"
                    self.repository.db.commit()
                    continue
                self.guard.register_attempt()
                self._persist_guard()
                session.execution_mode = self.mode.value
                budget = 2_000_000
                if (
                    session.execution_mode == LimitUpMode.AUTOMATIC.value
                    and self.worker is not None
                ):
                    budget = min(budget, int(self.worker.broker.get_account_balance().cash))
                try:
                    grid = build_grid(
                        upper_limit_price=machine.upper_limit_price,
                        budget_krw=budget,
                    )
                except ValueError:
                    machine.state = LimitUpState.CLOSED
                    session.state = LimitUpState.CLOSED.value
                    session.end_reason = "insufficient_budget"
                    self.repository.db.commit()
                    continue
                self._grids[ticker] = grid
                session.trigger_at = _utc_naive(now_kst)
                session.net_fired_at = _utc_naive(now_kst)
                if trade is not None:
                    session.trigger_turnover_krw = trade.cumulative_turnover_krw
                    session.trigger_strength = trade.execution_strength
                self.repository.transition(
                    session,
                    state=LimitUpState.NET_OPEN,
                    action="fire_net",
                    payload={
                        "turnover": session.trigger_turnover_krw,
                        "strength": session.trigger_strength,
                        "grid": [
                            {"name": leg.name, "price": leg.price, "quantity": leg.quantity}
                            for leg in grid
                        ],
                    },
                )
                self._dump_tape(ticker, "TRIGGER")
                self.repository.db.commit()
                if (
                    session.execution_mode == LimitUpMode.AUTOMATIC.value
                    and self.worker is not None
                ):
                    result = self.worker.fire_grid(
                        session,
                        grid,
                        daily_pnl_ratio=self.repository.daily_account_pnl_ratio(
                            now_kst.date()
                        ),
                    )
                    if result.owned_quantity > 0:
                        self._record_fill(
                            ticker,
                            result.owned_quantity,
                            trade.received_at if trade else 0.0,
                            now_kst,
                        )
                else:
                    for leg in grid:
                        self.repository.upsert_leg(
                            session,
                            name=leg.name,
                            price=leg.price,
                            quantity=leg.quantity,
                            status="recommended",
                        )
                    self.repository.db.commit()
                continue

            if command.kind is CommandKind.CANCEL_BUYS:
                if self.can_place_exit_for(ticker):
                    cancel = self._cancel_buys_safely(session)
                    if cancel is None:
                        return
                    if not cancel.is_clear:
                        # 취소하지 못한 매수가 브로커에 살아 있다. 지금 팔아도 그 주문이
                        # 나중에 체결되면 포지션이 되살아난다 — 사람이 볼 수 있게 잠근다.
                        self.manual_lock = True
                        logger.error(
                            "매수 취소 실패로 잔여 주문이 남았다 [%s] — 수동 잠금", ticker
                        )
                        return
                    result = cancel.reconciliation
                    # 취소가 체결과 경합하면 "미체결" 로 닫은 세션에 실제 주식이 남는다.
                    # 브로커가 정본이므로 그 수량을 보고 세션을 되살린다.
                    if result.owned_quantity > 0:
                        machine.adopt_late_fill(
                            at=self.monotonic_hint,
                            cumulative_quantity=result.owned_quantity,
                            avg_price=self.repository.average_fill_price(session),
                        )
                        if machine.state is LimitUpState.FILLED_WAIT_LOCK:
                            session.state = machine.state.value
                            self.repository.transition(
                                session,
                                state=LimitUpState.FILLED_WAIT_LOCK,
                                action="late_fill_adopted",
                                payload={"quantity": result.owned_quantity},
                            )
                            self.repository.db.commit()
                            continue
                if machine.state is LimitUpState.LOCKED:
                    self.repository.transition(
                        session, state=LimitUpState.LOCKED, action="locked"
                    )
                    session.locked_at = _utc_naive(now_kst)
                    self._dump_tape(ticker, "LOCKED")
                elif machine.state is LimitUpState.CLOSED:
                    self.repository.transition(
                        session,
                        state=LimitUpState.CLOSED,
                        action="cancel_no_fill",
                        payload={"reason": command.reason},
                    )
                    session.end_reason = command.reason
                    self._dump_tape(ticker, "CLOSED")
                self.repository.db.commit()
                continue

            if command.kind is CommandKind.MARKET_SELL:
                if machine.pattern_failure_pending and not session.pattern_failure_counted:
                    self._ensure_guard_for(now_kst.date())
                    self.guard.register_pattern_failure()
                    session.pattern_failure_counted = True
                    self._persist_guard()
                transition = "HARD_STOP" if command.reason == "hard_stop" else "TIME_STOP"
                self._dump_tape(ticker, transition)
                if self.can_place_exit_for(ticker):
                    result = self.worker.sell_actual_position(
                        session,
                        reason=command.reason,
                        owned_quantity=machine.filled_quantity,
                    )
                    # 결과를 버리면 세션이 RECONCILING 에 영원히 남아 슬롯을 잡고,
                    # 늦게 체결된 청산 손익이 NULL 로 남아 일일 중단선에 안 잡힌다.
                    if result.owned_quantity == 0:
                        machine.state = LimitUpState.CLOSED
                        machine.filled_quantity = 0
                        session.end_reason = command.reason
                elif self._is_virtual(ticker):
                    self._close_virtual(ticker, command.reason, now_kst.date())
                elif not self._strand_unprotected(ticker, command.reason):
                    machine.state = LimitUpState.CLOSED
                    session.end_reason = command.reason
                session.state = machine.state.value
                self.repository.db.commit()

    def _refresh_daily_pnl(self, ref_date: dt.date) -> None:
        """Roll the guard to ``ref_date`` if needed, then rebuild its loss latch.

        Called at the entry verdict rather than accumulated in memory, so a
        restarted process latches on the same realized loss the account took.
        Recommendation mode books no fills, so its sum stays 0.
        """
        self._ensure_guard_for(ref_date)
        self.guard.update_daily_pnl(self.repository.realized_pnl_total(ref_date))

    def _ensure_guard_for(self, ref_date: dt.date) -> None:
        """Keep exactly one guard per trading day, restored from persisted state.

        Two failure modes meet here. A process running across midnight would keep
        yesterday's latches and refuse to trade; a process restarted mid-session
        would start from zero and hand back attempts the day already spent. The
        guard is therefore rebuilt whenever the date changes — including the very
        first time — from what the database says already happened.

        Args:
            ref_date: KST trading date the engine is currently acting on.
        """
        if self.guard.ref_date == ref_date:
            return
        self.guard = DailyGuard(ref_date)
        row = self.repository.load_guard(ref_date)
        self.guard.restore(
            attempts=row.attempts,
            pattern_failures=row.pattern_failures,
            kosdaq_high=row.kosdaq_high,
            halted_reasons=row.halted_reasons or (),
        )
        self.repository.db.commit()

    def _persist_guard(self) -> None:
        """Write the live guard back so a restart cannot release its latches."""
        if self.guard.ref_date is None:
            return
        # 일시적 래치는 영속하지 않는다. feed_disconnected 는 연결이 살아나면 풀려야
        # 하는데, 저장해 두면 재시작이 그것을 되살려 그날을 통째로 막는다.
        self.repository.save_guard(
            self.guard.ref_date,
            attempts=self.guard.attempts,
            pattern_failures=self.guard.pattern_failures,
            kosdaq_high=self.guard.kosdaq_high,
            halted_reasons=self.guard.halted_reasons - _TRANSIENT_LATCHES,
        )
        self.repository.db.commit()

    def _dump_tape(self, ticker: str, transition: str) -> None:
        """Copy and persist one critical transition snapshot outside feed parsing."""
        self.repository.persist_tape(
            self._sessions[ticker], self.tape.snapshot(ticker, transition=transition)
        )

    def _active_count(self, *, exclude: str | None = None) -> int:
        """Count sessions that reserve a concurrent V1 slot."""
        states = {
            LimitUpState.NET_OPEN,
            LimitUpState.FILLED_WAIT_LOCK,
            LimitUpState.LOCKED,
            LimitUpState.EOD_REVIEW,
            LimitUpState.EOD_TRIM,
            LimitUpState.OVERNIGHT,
            LimitUpState.RECONCILING,
        }
        return sum(
            ticker != exclude and machine.state in states
            for ticker, machine in list(self._machines.items())
        )


def _utc_naive(value: dt.datetime) -> dt.datetime:
    """Normalize a wall-clock transition time to the repository convention."""
    if value.tzinfo is None:
        return value
    return value.astimezone(_UTC).replace(tzinfo=None)
