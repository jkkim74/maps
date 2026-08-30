"""Upper-limit V1 price, gate, and state-machine behavior."""

from __future__ import annotations

import pytest

from maps.limit_up.domain import (
    DAILY_LOSS_LIMIT_KRW,
    CommandKind,
    DailyGuard,
    LimitUpConfig,
    LimitUpMachine,
    LimitUpState,
    QuoteEvent,
    TradeEvent,
    build_grid,
    eod_hold_allowed,
    exit_audit_code,
    overnight_allowance,
    overnight_budget,
    realized_pnl,
    trigger_price,
)


def _config(**overrides) -> LimitUpConfig:
    values = {"min_turnover_krw": 50_000_000_000}
    values.update(overrides)
    return LimitUpConfig(**values)


def _trade(
    at: float,
    price: int,
    *,
    buy_initiated: bool = True,
    turnover: int = 50_000_000_000,
    strength: float = 150.0,
) -> TradeEvent:
    return TradeEvent(
        at=at,
        price=price,
        buy_initiated=buy_initiated,
        cumulative_turnover_krw=turnover,
        execution_strength=strength,
    )


def _quote(at: float, price: int, ask_qty: int) -> QuoteEvent:
    return QuoteEvent(at=at, price=price, best_ask_price=price, best_ask_qty=ask_qty)


def test_turnover_setting_cannot_cross_the_500eok_safety_floor() -> None:
    """A typo must not weaken the only V1 liquidity gate."""
    with pytest.raises(ValueError, match="50,000,000,000"):
        LimitUpConfig(min_turnover_krw=49_999_999_999)


def test_trigger_and_grid_use_upper_limit_ticks_and_ceil_rounding() -> None:
    """Wrong tick arithmetic would fire late or place off-grid buy prices."""
    assert trigger_price(39_450) == 39_300
    assert trigger_price(200_500) == 199_400

    grid = build_grid(upper_limit_price=39_450, budget_krw=2_000_000)

    assert [(leg.name, leg.price, leg.quantity) for leg in grid] == [
        ("S", 39_000, 30),
        ("A", 38_500, 20),
    ]


def test_every_v1_exit_reason_fits_the_order_log_column() -> None:
    reasons = {
        "hard_stop", "time_stop", "stuck_exit_retry", "recovered_exit",
        "eod_review_fail", "overnight_cap_unfilled", "eod_review_missed",
        "next_open", "after_hours_break_exit", "overnight_cap",
    }

    assert all(len(exit_audit_code(reason)) <= 16 for reason in reasons)


def test_entry_requires_buy_led_upward_cross_and_both_numeric_gates() -> None:
    """A downward touch, weak trade, or low turnover must not fire a net."""
    machine = LimitUpMachine("005930", upper_limit_price=13_000, config=_config())

    assert machine.on_trade(_trade(1.0, 12_960)) == []
    assert machine.on_trade(_trade(2.0, 12_970, buy_initiated=False)) == []
    assert machine.on_trade(_trade(3.0, 12_960)) == []
    assert machine.on_trade(_trade(4.0, 12_970, turnover=49_999_999_999)) == []
    assert machine.on_trade(_trade(5.0, 12_960)) == []
    assert machine.on_trade(_trade(6.0, 12_970, strength=149.99)) == []
    assert machine.on_trade(_trade(7.0, 12_960)) == []

    commands = machine.on_trade(_trade(8.0, 12_970))

    assert [command.kind for command in commands] == [CommandKind.FIRE_NET]
    assert machine.state is LimitUpState.NET_OPEN


def test_unfilled_net_is_cancelled_on_upper_limit_trade() -> None:
    """A stale pullback order must not survive after the first wave finishes."""
    machine = LimitUpMachine("005930", upper_limit_price=13_000, config=_config())
    machine.fire_net(at=10.0)

    commands = machine.on_trade(_trade(11.0, 13_000))

    assert [command.kind for command in commands] == [CommandKind.CANCEL_BUYS]
    assert machine.state is LimitUpState.CLOSED


def test_first_fill_starts_time_cut_and_ten_second_lock_cancels_remainder() -> None:
    """A one-tick upper-limit touch must not clear the 180-second time cut."""
    machine = LimitUpMachine("005930", upper_limit_price=13_000, config=_config())
    machine.fire_net(at=10.0)
    machine.on_fill(at=20.0, cumulative_quantity=1)

    assert machine.on_quote(_quote(25.0, 13_000, 0)) == []
    assert machine.on_quote(_quote(29.0, 12_990, 10)) == []
    assert machine.on_quote(_quote(30.0, 13_000, 0)) == []
    assert machine.on_timer(39.99) == []

    commands = machine.on_timer(40.0)

    assert [command.kind for command in commands] == [CommandKind.CANCEL_BUYS]
    assert machine.state is LimitUpState.LOCKED


def test_time_cut_cancels_then_sells_and_counts_pattern_failure() -> None:
    """A filled position that cannot lock in 180 seconds must exit once."""
    machine = LimitUpMachine("005930", upper_limit_price=13_000, config=_config())
    machine.fire_net(at=10.0)
    machine.on_fill(at=20.0, cumulative_quantity=5)

    commands = machine.on_timer(200.0)

    assert [command.kind for command in commands] == [
        CommandKind.CANCEL_BUYS,
        CommandKind.MARKET_SELL,
    ]
    assert machine.state is LimitUpState.RECONCILING
    assert machine.pattern_failure_pending is True


def test_hard_stop_has_priority_even_after_lock() -> None:
    """LOCKED must never disable the absolute upper-limit-relative stop."""
    machine = LimitUpMachine("005930", upper_limit_price=13_000, config=_config())
    assert machine.hard_stop_price == 12_350
    machine.fire_net(at=10.0)
    machine.on_fill(at=20.0, cumulative_quantity=5)
    machine.on_quote(_quote(21.0, 13_000, 0))
    machine.on_timer(31.0)
    assert machine.state is LimitUpState.LOCKED

    commands = machine.on_trade(_trade(32.0, 12_349, buy_initiated=False))

    assert [command.kind for command in commands] == [
        CommandKind.CANCEL_BUYS,
        CommandKind.MARKET_SELL,
    ]
    assert machine.state is LimitUpState.RECONCILING


def test_market_kill_cancels_pending_but_does_not_liquidate_filled_position() -> None:
    """The market guard blocks falling knives without dumping a refuge leader."""
    pending = LimitUpMachine("005930", upper_limit_price=13_000, config=_config())
    pending.fire_net(at=10.0)
    assert [c.kind for c in pending.on_market_halt(at=11.0)] == [CommandKind.CANCEL_BUYS]

    filled = LimitUpMachine("000660", upper_limit_price=130_000, config=_config())
    filled.fire_net(at=10.0)
    filled.on_fill(at=11.0, cumulative_quantity=1)
    assert filled.on_market_halt(at=12.0) == []
    assert filled.state is LimitUpState.FILLED_WAIT_LOCK


def test_daily_guard_latches_after_five_attempts_or_two_pattern_failures() -> None:
    """Daily limits must stop retries even when exits happened to make money."""
    attempts = DailyGuard()
    for _ in range(4):
        attempts.register_attempt()
        assert attempts.can_enter(active_sessions=0)
    attempts.register_attempt()
    assert attempts.can_enter(active_sessions=0) is False

    failures = DailyGuard()
    failures.register_pattern_failure()
    assert failures.can_enter(active_sessions=0)
    failures.register_pattern_failure()
    assert failures.can_enter(active_sessions=0) is False


def test_daily_guard_latches_kosdaq_drawdown_for_the_rest_of_the_day() -> None:
    """An index recovery must not reboot V1 after panic was observed."""
    guard = DailyGuard()
    assert guard.observe_kosdaq(1_000.0) is False
    assert guard.observe_kosdaq(985.1) is False
    assert guard.observe_kosdaq(985.0) is True
    assert guard.observe_kosdaq(1_010.0) is False
    assert guard.can_enter(active_sessions=0) is False


def test_daily_guard_blocks_two_active_sessions_and_account_loss() -> None:
    """Exposure and actual account loss must independently block a new grid."""
    guard = DailyGuard()
    assert guard.can_enter(active_sessions=2) is False
    guard.update_daily_pnl(-300_000)
    assert guard.can_enter(active_sessions=0) is False


def test_eod_hold_requires_fresh_upper_bid_one_percent_and_30eok() -> None:
    """A lower-level bid or stale quote must never authorize overnight risk."""
    assert eod_hold_allowed(
        upper_limit_price=30_000,
        best_bid_price=30_000,
        best_bid_qty=100_000,
        total_listed_shares=10_000_000,
        quote_fresh=True,
        shares_fresh=True,
    )
    assert not eod_hold_allowed(
        upper_limit_price=30_000,
        best_bid_price=29_950,
        best_bid_qty=100_000,
        total_listed_shares=10_000_000,
        quote_fresh=True,
        shares_fresh=True,
    )
    assert not eod_hold_allowed(
        upper_limit_price=30_000,
        best_bid_price=30_000,
        best_bid_qty=100_000,
        total_listed_shares=10_000_000,
        quote_fresh=False,
        shares_fresh=True,
    )


def test_grid_rejects_budget_that_cannot_create_both_legs() -> None:
    """A high-priced stock must not silently turn the fixed two-leg V1 into one leg."""
    with pytest.raises(ValueError, match="both grid legs"):
        build_grid(upper_limit_price=2_000_000, budget_krw=2_000_000)


def test_realized_pnl_charges_only_the_matched_quantity_after_costs() -> None:
    """A partial exit must not book the full entry cost as realized loss."""
    pnl = realized_pnl(
        buy_amount=1_000_000.0,
        buy_quantity=10,
        sell_amount=450_000.0,
        sell_quantity=5,
    )

    # entry 500,000 / exit 450,000 / fee 142.5 / sell tax 810
    assert pnl == pytest.approx(-50_952.5)


def test_realized_pnl_is_zero_while_nothing_is_sold() -> None:
    """An unsold position is unrealized, never a loss the daily latch reacts to."""
    assert realized_pnl(
        buy_amount=1_000_000.0, buy_quantity=10, sell_amount=0.0, sell_quantity=0
    ) == 0.0


def test_daily_guard_latches_at_the_confirmed_loss_limit() -> None:
    """-300,000 KRW is the confirmed requirement; the boundary itself must latch."""
    guard = DailyGuard()
    guard.update_daily_pnl(-299_999)
    assert guard.can_enter(active_sessions=0)

    guard.update_daily_pnl(DAILY_LOSS_LIMIT_KRW)
    assert not guard.can_enter(active_sessions=0)
    assert "daily_loss" in guard.halted_reasons


def test_overnight_budget_keeps_a_limit_down_inside_the_absolute_cap() -> None:
    """Exposure x 30% must never exceed -1,000,000 KRW, the emergency ceiling."""
    assert overnight_budget(0.0) == pytest.approx(3_333_333.33, abs=0.01)
    assert overnight_budget(0.0) * 0.30 == pytest.approx(1_000_000.0)

    # a realized loss has already spent part of the cap
    assert overnight_budget(-300_000.0) == pytest.approx(2_333_333.33, abs=0.01)
    assert 300_000 + overnight_budget(-300_000.0) * 0.30 == pytest.approx(1_000_000.0)


def test_overnight_budget_is_never_widened_by_profit() -> None:
    """A green morning must not fund a bigger overnight bet."""
    assert overnight_budget(500_000.0) == overnight_budget(0.0)


def test_overnight_budget_floors_at_zero_once_the_cap_is_spent() -> None:
    """Past the emergency ceiling nothing may be carried overnight."""
    assert overnight_budget(-1_000_000.0) == 0.0
    assert overnight_budget(-1_500_000.0) == 0.0


def test_overnight_allowance_splits_evenly_and_floors_to_whole_shares() -> None:
    """Two carried sessions each get half the budget, rounded down."""
    budget = overnight_budget(0.0)  # 3,333,333.33

    assert overnight_allowance(
        budget_krw=budget, session_count=2, upper_limit_price=100_000
    ) == 16
    assert overnight_allowance(
        budget_krw=budget, session_count=1, upper_limit_price=100_000
    ) == 33
    assert overnight_allowance(
        budget_krw=budget, session_count=2, upper_limit_price=2_000_000
    ) == 0


def test_hard_stop_is_anchored_to_entry_and_only_ever_widens() -> None:
    """effective_stop_price is entry-based; the limit price is only a pre-fill bound.

    Constraint 7: the stop may loosen, never tighten. Fills land below the limit
    (grid is -1.2%/-2.5%), so anchoring to the limit would clip the stop.
    """
    machine = LimitUpMachine("005930", upper_limit_price=100_000, config=LimitUpConfig())
    pre_fill = machine.hard_stop_price
    assert pre_fill == 95_000

    machine.fire_net(at=1.0)
    machine.on_fill(at=2.0, cumulative_quantity=20, avg_price=98_000.0)
    assert machine.hard_stop_price == 93_100  # 98,000 x 0.95 rounded down to tick
    assert machine.hard_stop_price < pre_fill

    # a later, higher average must not pull the stop back up
    machine.on_fill(at=3.0, cumulative_quantity=30, avg_price=99_000.0)
    assert machine.hard_stop_price == 93_100


def test_late_fill_adoption_anchors_stop_to_average_entry() -> None:
    machine = LimitUpMachine("005930", upper_limit_price=100_000, config=LimitUpConfig())
    machine.state = LimitUpState.CLOSED

    machine.adopt_late_fill(at=2.0, cumulative_quantity=20, avg_price=90_000.0)

    assert machine.hard_stop_price == 85_500
