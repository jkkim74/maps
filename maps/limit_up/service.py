"""Application service that binds V1 market events, state, and broker commands."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import Enum

from maps.common.models import LimitUpSession
from maps.common.settings import MapsSettings
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
from maps.limit_up.feed import FeedQuote, FeedTrade, TapeBuffer
from maps.limit_up.repository import LimitUpRepository
from maps.limit_up.worker import LimitUpCommandWorker


_UTC = dt.timezone.utc


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
        # 마지막으로 관측한 단조 시각. 늦은 체결을 되살릴 때 시간 원점으로 쓴다.
        self.monotonic_hint = 0.0

    def watch_candidate(self, candidate: Candidate, *, now_kst: dt.datetime) -> bool:
        """Start watching an eligible +25% common-share candidate in entry hours."""
        if self.mode is LimitUpMode.OFF or self.manual_lock or not candidate.eligible:
            return False
        if candidate.market not in {"KOSPI", "KOSDAQ"} or candidate.change_rate < 25.0:
            return False
        if not dt.time(9, 10) <= now_kst.timetz().replace(tzinfo=None) <= dt.time(14, 30):
            return False
        if candidate.ticker in self._machines:
            return False
        session = self.repository.create_or_get_session(
            ref_date=now_kst.date(),
            ticker=candidate.ticker,
            market=candidate.market,
            upper_limit_price=candidate.upper_limit_price,
            trigger_price=trigger_price(candidate.upper_limit_price),
            total_listed_shares=candidate.total_listed_shares,
        )
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
        return True

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
        if self.mode is LimitUpMode.RECOMMEND_ONLY:
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
            if (
                self.mode is LimitUpMode.AUTOMATIC
                and self.worker is not None
                and now_monotonic - last_reconcile >= 1.0
            ):
                if machine.state in {LimitUpState.NET_OPEN, LimitUpState.FILLED_WAIT_LOCK}:
                    self._last_reconcile_at[ticker] = now_monotonic
                    result = self.worker.reconcile(self._sessions[ticker])
                    if result.position_quantity > machine.filled_quantity:
                        self._record_fill(ticker, result.position_quantity, now_monotonic, wall)
                elif machine.state is LimitUpState.RECONCILING:
                    # 청산은 제출 시점에 체결되지 않는 일이 흔하다. 여기서 계속 확인하지
                    # 않으면 세션이 RECONCILING 에 영원히 남아 슬롯을 잡고, 늦게 잡힌
                    # 청산 손익이 NULL 로 남아 일일 중단선에 반영되지 않는다.
                    self._last_reconcile_at[ticker] = now_monotonic
                    session = self._sessions[ticker]
                    result = self.worker.reconcile(session)
                    if result.position_quantity == 0:
                        machine.state = LimitUpState.CLOSED
                        machine.filled_quantity = 0
                        session.state = LimitUpState.CLOSED.value
                        self.repository.db.commit()
            commands = machine.on_timer(now_monotonic)
            self._handle_commands(ticker, commands, now_kst=wall)

    def on_kosdaq(self, *, value: float, at: float) -> None:
        """Latch panic drawdown and cancel every still-pending V1 buy grid."""
        if not self.guard.observe_kosdaq(value):
            return
        now = dt.datetime.now(dt.timezone(dt.timedelta(hours=9)))
        for ticker, machine in self._machines.items():
            commands = machine.on_market_halt(at=at)
            self._handle_commands(ticker, commands, now_kst=now)

    def on_feed_disconnect(self, *, at: float, now_kst: dt.datetime) -> None:
        """Fail-close new entries and pull only unfilled nets on feed loss."""
        self.guard.halted_reasons.add("feed_disconnected")
        for ticker, machine in self._machines.items():
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
        if self.mode is LimitUpMode.AUTOMATIC and self.worker is not None:
            self.worker.sell_actual_position(session, reason="eod_review_fail")
        else:
            machine.state = LimitUpState.CLOSED
            session.state = LimitUpState.CLOSED.value
            session.end_reason = "eod_review_fail"
        self.repository.db.commit()
        return "sell"

    def emergency_off(self) -> None:
        """Latch every entry path off immediately.

        Entries only. Exits stay available — a kill switch that also blocks
        selling would strand whatever is already held.
        """
        self.mode = LimitUpMode.OFF
        self.guard.halted_reasons.add("emergency_off")

    def carried_tickers(self) -> list[str]:
        """Return sessions still competing for the shared overnight budget."""
        return sorted(
            ticker
            for ticker, machine in self._machines.items()
            if machine.state in {LimitUpState.OVERNIGHT, LimitUpState.EOD_TRIM}
        )

    def overnight_allowances(self, ref_date: dt.date) -> dict[str, int]:
        """Return each carried session's share cap under today's risk budget.

        Recomputed at every checkpoint rather than frozen at 15:18, so a session
        closing in between re-splits the budget across whoever is left.
        """
        tickers = self.carried_tickers()
        budget = overnight_budget(self.repository.realized_pnl_total(ref_date))
        return {
            ticker: overnight_allowance(
                budget_krw=budget,
                session_count=len(tickers),
                upper_limit_price=self._machines[ticker].upper_limit_price,
            )
            for ticker in tickers
        }

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
            if self.mode is LimitUpMode.AUTOMATIC and self.worker is not None:
                # tick() only reconciles NET_OPEN/FILLED_WAIT_LOCK, so a locked
                # session's cached quantity can lag the actual holding. Sizing the
                # trim off a stale number would under-trim and breach the cap.
                machine.filled_quantity = self.worker.reconcile(session).position_quantity
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
            if self.mode is LimitUpMode.AUTOMATIC and self.worker is not None:
                self.worker.sell_overnight_excess(
                    session, quantity=excess, price=machine.upper_limit_price
                )
            else:
                machine.filled_quantity = allowed
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
            if self.mode is LimitUpMode.AUTOMATIC and self.worker is not None:
                machine.filled_quantity = self.worker.reconcile(session).position_quantity
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
        del ref_date
        liquidated: list[str] = []
        # EOD_TRIM(캡 미체결)뿐 아니라 LOCKED 도 대상이다. 장애·재시작으로 15:18 창을
        # 놓치면 심사 자체를 못 받은 LOCKED 세션이 상한 적용 없이 익일로 넘어간다.
        # 심사받지 않은 포지션은 오버나이트 자격이 없다 — fail-closed.
        stranded = {LimitUpState.EOD_TRIM, LimitUpState.LOCKED}
        candidates = sorted(
            ticker
            for ticker, machine in self._machines.items()
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
            if self.mode is LimitUpMode.AUTOMATIC and self.worker is not None:
                self.worker.cancel_open_exits(session)
                result = self.worker.sell_actual_position(session, reason=reason)
                if result.position_quantity == 0:
                    machine.state = LimitUpState.CLOSED
                    machine.filled_quantity = 0
            else:
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
        if self.worker is None:
            return
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
        known = {row.ticker for row in rows}
        actual = self.worker.broker.get_positions()
        self.unknown_positions = sorted(
            ticker for ticker, quantity in actual.items() if quantity > 0 and ticker not in known
        )
        if self.unknown_positions:
            self.manual_lock = True
        for row in rows:
            machine = LimitUpMachine(
                row.ticker,
                upper_limit_price=row.upper_limit_price,
                config=self.config,
            )
            machine.state = LimitUpState(row.state)
            position_qty = actual.get(row.ticker, 0)
            machine.filled_quantity = position_qty
            if row.first_fill_at is not None:
                first_fill = row.first_fill_at.replace(tzinfo=_UTC)
                elapsed = max(0.0, (wall.astimezone(_UTC) - first_fill).total_seconds())
                machine.first_fill_at = now_monotonic - elapsed
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
            if position_qty == 0 and row.state in {
                LimitUpState.FILLED_WAIT_LOCK.value,
                LimitUpState.LOCKED.value,
                LimitUpState.OVERNIGHT.value,
            }:
                row.state = LimitUpState.RECONCILING.value
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
                for ticker, machine in self._machines.items()
            },
        }

    def watched_tickers(self) -> tuple[str, ...]:
        """Return every persisted live ticker in deterministic order."""
        return tuple(sorted(self._machines))

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
            for ticker, machine in self._machines.items()
            if machine.filled_quantity > 0 and machine.state in held_states
        )

    def locked_tickers(self) -> list[str]:
        """Return sessions eligible for the strict 15:18 review."""
        return sorted(
            ticker
            for ticker, machine in self._machines.items()
            if machine.state is LimitUpState.LOCKED
        )

    def overnight_tickers(self) -> list[str]:
        """Return sessions that must submit the next opening-auction exit.

        ``AFTER_HOURS_EXIT`` is included: an after-hours escape that never filled
        still holds shares, and leaving it out would strand the position for
        another whole day — the exact risk the escape existed to avoid.
        """
        return sorted(
            ticker
            for ticker, machine in self._machines.items()
            if machine.state in {LimitUpState.OVERNIGHT, LimitUpState.AFTER_HOURS_EXIT}
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
        if self.mode is LimitUpMode.AUTOMATIC and self.worker is not None:
            self.worker.sell_actual_position(session, reason="next_open")
        else:
            machine.state = LimitUpState.CLOSED
            session.state = LimitUpState.CLOSED.value
            session.end_reason = "next_open"
        self.repository.db.commit()

    def control_lost(self) -> bool:
        """Return whether exposure exists without trustworthy automation control."""
        feed_lost = "feed_disconnected" in self.guard.halted_reasons
        return self.manual_lock or (feed_lost and bool(self.held_tickers()))

    def _apply_virtual_fills(
        self, ticker: str, price: int, at: float, now_kst: dt.datetime
    ) -> None:
        """Apply deterministic touch fills only in recommendation mode."""
        machine = self._machines[ticker]
        if machine.state is not LimitUpState.NET_OPEN:
            return
        filled_legs = self._virtual_filled_legs.setdefault(ticker, set())
        cumulative = machine.filled_quantity
        for leg in self._grids.get(ticker, ()):
            if leg.name not in filled_legs and price <= leg.price:
                filled_legs.add(leg.name)
                cumulative += leg.quantity
        if cumulative > machine.filled_quantity:
            self._record_fill(ticker, cumulative, at, now_kst)

    def _record_fill(
        self, ticker: str, quantity: int, at: float, now_kst: dt.datetime
    ) -> None:
        """Record the first broker or virtual fill and force a tape dump."""
        machine = self._machines[ticker]
        first = machine.first_fill_at is None
        machine.on_fill(at=at, cumulative_quantity=quantity)
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
                budget = 2_000_000
                if self.worker is not None:
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
                    },
                )
                self._dump_tape(ticker, "TRIGGER")
                self.repository.db.commit()
                if self.mode is LimitUpMode.AUTOMATIC and self.worker is not None:
                    result = self.worker.fire_grid(session, grid)
                    if result.position_quantity > 0:
                        self._record_fill(
                            ticker, result.position_quantity, trade.received_at if trade else 0.0, now_kst
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
                if self.mode is LimitUpMode.AUTOMATIC and self.worker is not None:
                    result = self.worker.cancel_pending_buys(session)
                    # 취소가 체결과 경합하면 "미체결" 로 닫은 세션에 실제 주식이 남는다.
                    # 브로커가 정본이므로 그 수량을 보고 세션을 되살린다.
                    if result.position_quantity > 0:
                        machine.adopt_late_fill(
                            at=self.monotonic_hint,
                            cumulative_quantity=result.position_quantity,
                        )
                        if machine.state is LimitUpState.FILLED_WAIT_LOCK:
                            session.state = machine.state.value
                            self.repository.transition(
                                session,
                                state=LimitUpState.FILLED_WAIT_LOCK,
                                action="late_fill_adopted",
                                payload={"quantity": result.position_quantity},
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
                    self.guard.register_pattern_failure()
                    session.pattern_failure_counted = True
                transition = "HARD_STOP" if command.reason == "hard_stop" else "TIME_STOP"
                self._dump_tape(ticker, transition)
                if self.mode is LimitUpMode.AUTOMATIC and self.worker is not None:
                    result = self.worker.sell_actual_position(
                        session, reason=command.reason
                    )
                    # 결과를 버리면 세션이 RECONCILING 에 영원히 남아 슬롯을 잡고,
                    # 늦게 체결된 청산 손익이 NULL 로 남아 일일 중단선에 안 잡힌다.
                    if result.position_quantity == 0:
                        machine.state = LimitUpState.CLOSED
                        machine.filled_quantity = 0
                        session.end_reason = command.reason
                else:
                    machine.state = LimitUpState.CLOSED
                    session.end_reason = command.reason
                session.state = machine.state.value
                self.repository.db.commit()

    def _refresh_daily_pnl(self, ref_date: dt.date) -> None:
        """Rebuild the daily loss latch from persisted session P/L.

        Called at the entry verdict rather than accumulated in memory, so a
        restarted process latches on the same realized loss the account took.
        Recommendation mode books no fills, so its sum stays 0.
        """
        self.guard.update_daily_pnl(self.repository.realized_pnl_total(ref_date))

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
            for ticker, machine in self._machines.items()
        )


def _utc_naive(value: dt.datetime) -> dt.datetime:
    """Normalize a wall-clock transition time to the repository convention."""
    if value.tzinfo is None:
        return value
    return value.astimezone(_UTC).replace(tzinfo=None)
