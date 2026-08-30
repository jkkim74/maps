"""Pure price rules and state transitions for the upper-limit V1 engine."""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum

from maps.backtest.cost_model import BROKER_FEE_PER_SIDE, TRANSACTION_TAX_SELL
from maps.market.trading_rules import krx_tick_size, round_up_krx_price
from maps.strategy.live_rules import effective_stop_price


MIN_TURNOVER_FLOOR_KRW = 50_000_000_000

# 확정 요구값이라 설정으로 열지 않는다. 유동성 하한과 같은 방침이다 —
# 설정으로 열어 두면 위험 한도가 조용히 느슨해진다.
DAILY_LOSS_LIMIT_KRW = -300_000

# 비상 절대상한과 익일 하한가. 오버나이트 노출의 수학적 보증이라 설정으로 열지 않는다.
ABSOLUTE_CAP_KRW = 1_000_000
LIMIT_DOWN_RATIO = 0.30

# 시간외 탈출 지정가 = 종가 × 0.90 (시간외 하한). 단일가 매매라 지정가는 체결
# *우선순위*만 정하고 체결가는 그 회차 단일가로 난다.
AFTER_HOURS_FLOOR_RATIO = 0.90

# 청산 주문의 전략 ID. **사유마다 달라야 한다.**
# OrderManager._raise_if_duplicate_active_order 는 같은 날 같은 strategy_id+ticker+side 가
# pending/partially_filled/**filled** 로 있으면 거부한다. 청산 전부가 한 ID 를 쓰면
# 15:18 트림이 체결된 순간 15:28 강제청산·하드스톱·시간외 탈출이 전부 막힌다
# (매수 레그가 :S/:A 로 나뉜 것과 같은 이유다).
EXIT_STRATEGY_IDS: dict[str, str] = {
    "trim": "limit_up_v1:exit:trim",
    "stop": "limit_up_v1:exit:stop",
    "eod": "limit_up_v1:exit:eod",
    "next_open": "limit_up_v1:exit:next_open",
    "after_hours": "limit_up_v1:exit:after_hours",
}
_EXIT_REASON_KINDS: dict[str, str] = {
    "hard_stop": "stop",
    "time_stop": "stop",
    "stuck_exit_retry": "stop",
    "recovered_exit": "stop",
    "eod_review_fail": "eod",
    "overnight_cap_unfilled": "eod",
    "eod_review_missed": "eod",
    "next_open": "next_open",
    "after_hours_break_exit": "after_hours",
}
_EXIT_AUDIT_CODES: dict[str, str] = {
    "overnight_cap_unfilled": "cap_unfilled",
    "after_hours_break_exit": "after_hours",
    "eod_review_missed": "eod_missed",
    "eod_review_fail": "eod_fail",
    "upper_limit_without_fill": "no_fill_limit",
}


def exit_strategy_id(reason: str) -> str:
    """Return the order strategy id for one exit reason.

    Args:
        reason: Exit reason recorded on the session.

    Returns:
        A reason-scoped strategy id; unknown reasons fall back to the stop lane.
    """
    return EXIT_STRATEGY_IDS[_EXIT_REASON_KINDS.get(reason, "stop")]


def exit_audit_code(reason: str) -> str:
    """Return the compact OrderLog code while session.end_reason stays detailed."""
    code = _EXIT_AUDIT_CODES.get(reason, reason)
    if len(code) > 16:
        raise ValueError(f"limit-up exit audit code exceeds 16 characters: {code}")
    return code


class LimitUpState(str, Enum):
    """Persisted lifecycle states for one ticker and trading day."""

    WATCHING = "watching"
    NET_OPEN = "net_open"
    FILLED_WAIT_LOCK = "filled_wait_lock"
    LOCKED = "locked"
    EOD_REVIEW = "eod_review"
    EOD_TRIM = "eod_trim"
    AFTER_HOURS_EXIT = "after_hours_exit"
    OVERNIGHT = "overnight"
    CLOSED = "closed"
    RECONCILING = "reconciling"
    MANUAL_LOCK = "manual_lock"


class CommandKind(str, Enum):
    """Side effects requested by the pure state machine."""

    FIRE_NET = "fire_net"
    CANCEL_BUYS = "cancel_buys"
    MARKET_SELL = "market_sell"


@dataclass(frozen=True)
class LimitUpConfig:
    """Validated V1 constants with only turnover configurable upward."""

    min_turnover_krw: int = MIN_TURNOVER_FLOOR_KRW
    min_execution_strength: float = 150.0
    no_fill_timeout_seconds: float = 180.0
    fill_timeout_seconds: float = 180.0
    lock_seconds: float = 10.0

    def __post_init__(self) -> None:
        """Reject any setting that weakens the hard liquidity floor."""
        if self.min_turnover_krw < MIN_TURNOVER_FLOOR_KRW:
            raise ValueError("min_turnover_krw must be at least 50,000,000,000")


@dataclass(frozen=True)
class GridLeg:
    """One fixed V1 limit-buy leg."""

    name: str
    budget_krw: int
    price: int
    quantity: int


@dataclass(frozen=True)
class TradeEvent:
    """Normalized real-time execution event."""

    at: float
    price: int
    buy_initiated: bool
    cumulative_turnover_krw: int
    execution_strength: float


@dataclass(frozen=True)
class QuoteEvent:
    """Normalized best-ask snapshot."""

    at: float
    price: int
    best_ask_price: int
    best_ask_qty: int


@dataclass(frozen=True)
class MachineCommand:
    """Idempotent side-effect intent emitted by the state machine."""

    kind: CommandKind
    reason: str


def trigger_price(upper_limit_price: int) -> int:
    """Return exactly three quotation ticks below the broker upper limit."""
    price = upper_limit_price
    for _ in range(3):
        price -= krx_tick_size(price)
    return price


def build_grid(*, upper_limit_price: int, budget_krw: int) -> tuple[GridLeg, GridLeg]:
    """Build the fixed 60/40 S/A grid using ceil-to-tick prices."""
    if upper_limit_price <= 0 or budget_krw <= 0:
        raise ValueError("upper_limit_price and budget_krw must be positive")
    specs = (("S", 0.60, 0.012), ("A", 0.40, 0.025))
    legs: list[GridLeg] = []
    for name, weight, discount in specs:
        leg_budget = math.floor(budget_krw * weight)
        price = round_up_krx_price(upper_limit_price * (1.0 - discount))
        legs.append(
            GridLeg(
                name=name,
                budget_krw=leg_budget,
                price=price,
                quantity=leg_budget // price,
            )
        )
    if any(leg.quantity <= 0 for leg in legs):
        raise ValueError("budget must buy at least one share in both grid legs")
    return legs[0], legs[1]


def realized_pnl(
    *,
    buy_amount: float,
    buy_quantity: int,
    sell_amount: float,
    sell_quantity: int,
) -> float:
    """Return net proceeds for the sold portion after fees and the KRX sell tax.

    Only the matched quantity is realized. An unsold remainder stays unrealized,
    so a partial exit must not be charged the full entry cost.

    Args:
        buy_amount: Filled buy value in KRW.
        buy_quantity: Filled buy quantity.
        sell_amount: Filled sell value in KRW.
        sell_quantity: Filled sell quantity.

    Returns:
        Net realized profit or loss in KRW; ``0.0`` when nothing is matched.
    """
    matched = min(buy_quantity, sell_quantity)
    if matched <= 0 or buy_quantity <= 0 or sell_quantity <= 0:
        return 0.0
    entry = (buy_amount / buy_quantity) * matched
    exit_value = (sell_amount / sell_quantity) * matched
    fees = (entry + exit_value) * BROKER_FEE_PER_SIDE
    tax = exit_value * TRANSACTION_TAX_SELL
    return exit_value - entry - fees - tax


def overnight_budget(realized_pnl_today: float) -> float:
    """Return the KRW exposure that keeps a next-day limit-down inside the cap.

    Realized *profit* never widens the budget. Letting a good morning fund a
    bigger overnight bet makes the absolute cap depend on the day's luck.

    Args:
        realized_pnl_today: Net realized P/L booked so far today, in KRW.

    Returns:
        Maximum total overnight exposure in KRW, measured at today's close.
    """
    spent = max(0.0, -realized_pnl_today)
    return max(0.0, ABSOLUTE_CAP_KRW - spent) / LIMIT_DOWN_RATIO


def overnight_allowance(
    *, budget_krw: float, session_count: int, upper_limit_price: int
) -> int:
    """Return how many shares one of N equally-funded sessions may carry over.

    Args:
        budget_krw: Total overnight budget shared by every held session.
        session_count: Number of sessions splitting that budget.
        upper_limit_price: Today's close, which is the upper limit price.

    Returns:
        Allowed share count, floored; ``0`` when the budget buys nothing.
    """
    if session_count <= 0 or upper_limit_price <= 0 or budget_krw <= 0:
        return 0
    return int((budget_krw / session_count) // upper_limit_price)


def eod_hold_allowed(
    *,
    upper_limit_price: int,
    best_bid_price: int,
    best_bid_qty: int,
    total_listed_shares: int,
    quote_fresh: bool,
    shares_fresh: bool,
) -> bool:
    """Return the strict V1 overnight verdict for one fresh EOD snapshot."""
    if not quote_fresh or not shares_fresh or total_listed_shares <= 0:
        return False
    if best_bid_price != upper_limit_price or best_bid_qty <= 0:
        return False
    quantity_ratio = best_bid_qty / total_listed_shares
    bid_value = best_bid_qty * upper_limit_price
    return quantity_ratio >= 0.01 and bid_value >= 3_000_000_000


class DailyGuard:
    """Latched per-day entry guard shared by all V1 ticker sessions.

    Carries its trading day so a process running across midnight cannot keep
    yesterday's latches, and exposes ``restore`` so a mid-session restart cannot
    wipe limits that have already fired.
    """

    def __init__(self, ref_date: "dt.date | None" = None) -> None:
        """Create a fresh guard for one trading day.

        Args:
            ref_date: KST trading date this guard belongs to.
        """
        self.ref_date = ref_date
        self.attempts = 0
        self.pattern_failures = 0
        self.daily_pnl = 0.0
        self.kosdaq_high: float | None = None
        self.halted_reasons: set[str] = set()

    def restore(
        self,
        *,
        attempts: int,
        pattern_failures: int,
        kosdaq_high: float | None = None,
        halted_reasons: "Iterable[str] | None" = None,
    ) -> None:
        """Re-apply everything that already fired before a restart.

        Starting from zero mid-session would silently hand back attempts the day
        had already spent. The index high matters too: rebuilding it from the
        post-restart tape starts from an already-depressed price, so a drawdown
        that should have latched never does.

        Args:
            attempts: Net attempts already made today.
            pattern_failures: Hard/time exits already counted today.
            kosdaq_high: Intraday index high seen before the restart.
            halted_reasons: Latches that were already set.
        """
        self.attempts = max(self.attempts, attempts)
        self.pattern_failures = max(self.pattern_failures, pattern_failures)
        if kosdaq_high is not None and kosdaq_high > 0:
            self.kosdaq_high = max(self.kosdaq_high or 0.0, kosdaq_high)
        self.halted_reasons.update(halted_reasons or ())
        if self.attempts >= 5:
            self.halted_reasons.add("max_attempts")
        if self.pattern_failures >= 2:
            self.halted_reasons.add("pattern_failures")

    def register_attempt(self) -> None:
        """Count a durable net command and latch at the fifth attempt."""
        self.attempts += 1
        if self.attempts >= 5:
            self.halted_reasons.add("max_attempts")

    def register_pattern_failure(self) -> None:
        """Count hard/time exits regardless of their realized P/L sign."""
        self.pattern_failures += 1
        if self.pattern_failures >= 2:
            self.halted_reasons.add("pattern_failures")

    def update_daily_pnl(self, value: float) -> None:
        """Latch new entries at the account's normal daily loss limit."""
        self.daily_pnl = value
        if value <= DAILY_LOSS_LIMIT_KRW:
            self.halted_reasons.add("daily_loss")

    def observe_kosdaq(self, value: float) -> bool:
        """Update the intraday high and report a newly latched 1.5% drawdown."""
        if value <= 0:
            return False
        if self.kosdaq_high is None or value > self.kosdaq_high:
            self.kosdaq_high = value
        if "kosdaq_drawdown" in self.halted_reasons or self.kosdaq_high is None:
            return False
        drawdown = (self.kosdaq_high - value) / self.kosdaq_high
        if drawdown >= 0.015:
            self.halted_reasons.add("kosdaq_drawdown")
            return True
        return False

    def can_enter(self, *, active_sessions: int) -> bool:
        """Return whether both daily and concurrent-session gates are open."""
        return not self.halted_reasons and active_sessions < 2


class LimitUpMachine:
    """Pure single-session state machine driven by normalized market events."""

    def __init__(
        self,
        ticker: str,
        *,
        upper_limit_price: int,
        config: LimitUpConfig,
    ) -> None:
        """Create an untriggered WATCHING session."""
        if not ticker or upper_limit_price <= 0:
            raise ValueError("ticker and upper_limit_price are required")
        self.ticker = ticker
        self.upper_limit_price = upper_limit_price
        self.config = config
        # 정본 함수는 **진입가** 기준이다. 체결 전에는 진입가를 모르므로 가능한 최고
        # 진입가(상한가)로 두고, 첫 체결에서 실제 평균가로 **넓히기만** 한다 — 좁히면
        # 제약 7 이 경고하는 "손절이 조여지는" 방향이다.
        self.hard_stop_price = self._stop_for(upper_limit_price)
        self.entry_price: float | None = None
        self.state = LimitUpState.WATCHING
        self.last_trade_price: int | None = None
        self.net_fired_at: float | None = None
        self.first_fill_at: float | None = None
        self.lock_started_at: float | None = None
        self.filled_quantity = 0
        self.pattern_failure_pending = False
        self.market_halted = False

    def fire_net(self, *, at: float) -> None:
        """Record one net attempt before the command worker submits orders."""
        if self.state is not LimitUpState.WATCHING:
            raise ValueError(f"cannot fire net from {self.state.value}")
        self.state = LimitUpState.NET_OPEN
        self.net_fired_at = at

    def on_trade(self, event: TradeEvent) -> list[MachineCommand]:
        """Apply an execution event and return ordered side-effect intents."""
        if self._hard_stop_crossed(event.price):
            return self._protective_exit("hard_stop")

        if self.state is LimitUpState.NET_OPEN and self.filled_quantity == 0:
            if event.price == self.upper_limit_price:
                self.state = LimitUpState.CLOSED
                return [MachineCommand(CommandKind.CANCEL_BUYS, "upper_limit_without_fill")]

        previous = self.last_trade_price
        self.last_trade_price = event.price
        if self.state is not LimitUpState.WATCHING or self.market_halted:
            return []
        crossed = (
            previous is not None
            and previous < trigger_price(self.upper_limit_price) <= event.price
        )
        gates_pass = (
            event.buy_initiated
            and event.cumulative_turnover_krw >= self.config.min_turnover_krw
            and event.execution_strength >= self.config.min_execution_strength
        )
        if not crossed or not gates_pass:
            return []
        self.fire_net(at=event.at)
        return [MachineCommand(CommandKind.FIRE_NET, "three_tick_upward_cross")]

    def on_quote(self, event: QuoteEvent) -> list[MachineCommand]:
        """Track continuous upper-limit locking and quote-driven hard stops."""
        if self._hard_stop_crossed(event.price):
            return self._protective_exit("hard_stop")
        if self.state is not LimitUpState.FILLED_WAIT_LOCK:
            return []
        locked_now = event.price == self.upper_limit_price and event.best_ask_qty == 0
        if locked_now:
            if self.lock_started_at is None:
                self.lock_started_at = event.at
        else:
            self.lock_started_at = None
        return []

    @staticmethod
    def _stop_for(entry_price: float) -> int:
        """Return the canonical hard stop for one entry price."""
        stop = effective_stop_price("limit_up_v1", entry_price)
        if stop is None:
            raise ValueError("limit_up_v1 hard stop is not configured")
        return int(stop)

    def on_fill(
        self, *, at: float, cumulative_quantity: int, avg_price: float | None = None
    ) -> None:
        """Start the time cut on the first broker-confirmed share.

        Args:
            at: Monotonic fill time.
            cumulative_quantity: Session-owned shares after this fill.
            avg_price: Average entry price; re-anchors the stop, wider only.
        """
        if cumulative_quantity <= 0:
            return
        if avg_price and avg_price > 0:
            self.entry_price = avg_price
            self.hard_stop_price = min(self.hard_stop_price, self._stop_for(avg_price))
        self.filled_quantity = max(self.filled_quantity, cumulative_quantity)
        if self.first_fill_at is None:
            self.first_fill_at = at
        if self.state in {LimitUpState.NET_OPEN, LimitUpState.RECONCILING}:
            self.state = LimitUpState.FILLED_WAIT_LOCK

    def adopt_late_fill(self, *, at: float, cumulative_quantity: int) -> None:
        """Reopen a session closed as unfilled that the broker actually filled.

        A cancel racing a fill can leave real shares behind a CLOSED session. Those
        shares would then sit outside every protection path — hard stop, REST
        fallback, EOD review — so the session has to come back to a held state.

        Args:
            at: Monotonic time of the confirming reconciliation.
            cumulative_quantity: Broker-authoritative held quantity.
        """
        if cumulative_quantity <= 0 or self.state is not LimitUpState.CLOSED:
            return
        self.state = LimitUpState.FILLED_WAIT_LOCK
        self.on_fill(at=at, cumulative_quantity=cumulative_quantity)

    def on_timer(self, now: float) -> list[MachineCommand]:
        """Apply no-fill expiry, lock confirmation, or filled time cut."""
        if (
            self.state is LimitUpState.NET_OPEN
            and self.filled_quantity == 0
            and self.net_fired_at is not None
            and now - self.net_fired_at >= self.config.no_fill_timeout_seconds
        ):
            self.state = LimitUpState.CLOSED
            return [MachineCommand(CommandKind.CANCEL_BUYS, "no_fill_timeout")]
        if self.state is not LimitUpState.FILLED_WAIT_LOCK or self.first_fill_at is None:
            return []
        deadline = self.first_fill_at + self.config.fill_timeout_seconds
        if (
            self.lock_started_at is not None
            and self.lock_started_at + self.config.lock_seconds <= min(now, deadline)
        ):
            self.state = LimitUpState.LOCKED
            self.lock_started_at = None
            return [MachineCommand(CommandKind.CANCEL_BUYS, "locked")]
        if now >= deadline:
            return self._protective_exit("time_stop")
        return []

    def on_market_halt(self, *, at: float) -> list[MachineCommand]:
        """Latch new-entry halt and pull only still-pending buy orders."""
        del at
        self.market_halted = True
        if self.state is LimitUpState.NET_OPEN and self.filled_quantity == 0:
            self.state = LimitUpState.CLOSED
            return [MachineCommand(CommandKind.CANCEL_BUYS, "market_halt")]
        return []

    def _hard_stop_crossed(self, price: int) -> bool:
        """Return whether a held position crossed below the absolute stop."""
        held_state = self.state in {
            LimitUpState.FILLED_WAIT_LOCK,
            LimitUpState.LOCKED,
            LimitUpState.EOD_REVIEW,
            LimitUpState.OVERNIGHT,
        }
        return held_state and price < self.hard_stop_price

    def _protective_exit(self, reason: str) -> list[MachineCommand]:
        """Enter reconciliation and request cancel-before-sell exactly once."""
        if self.state is LimitUpState.RECONCILING:
            return []
        self.state = LimitUpState.RECONCILING
        self.pattern_failure_pending = reason in {"hard_stop", "time_stop"}
        return [
            MachineCommand(CommandKind.CANCEL_BUYS, reason),
            MachineCommand(CommandKind.MARKET_SELL, reason),
        ]
