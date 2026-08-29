"""End-to-end V1 service behavior with a deterministic broker boundary."""

from __future__ import annotations

import datetime as dt

from maps.execution.broker_adapter import (
    AccountBalance,
    BrokerAdapter,
    Order,
    OrderResult,
    OrderSide,
    OrderStatus,
    PendingOrder,
    Position,
)
from maps.execution.order_manager import OrderManager
from maps.limit_up.domain import LimitUpConfig, LimitUpState
from maps.limit_up.feed import FeedQuote, FeedTrade
from maps.limit_up.repository import LimitUpRepository
from maps.limit_up.service import Candidate, LimitUpMode, LimitUpService
from maps.limit_up.worker import LimitUpCommandWorker
from maps.risk.manager import RiskManager


KST = dt.timezone(dt.timedelta(hours=9))


class ServiceBroker(BrokerAdapter):
    """Pending-order broker that exposes actual orders and positions."""

    def __init__(self) -> None:
        """Create a flat 20m KRW paper account."""
        self.orders: list[tuple[str, Order]] = []
        self.cancelled: set[str] = set()
        self.positions: dict[str, Position] = {}

    def place_order(self, order: Order) -> OrderResult:
        """Accept every submitted order as pending."""
        order_id = str(2000 + len(self.orders) + 1)
        self.orders.append((order_id, order))
        return OrderResult(
            order_id=order_id,
            strategy_id=order.strategy_id,
            ticker=order.ticker,
            side=order.side,
            status=OrderStatus.PENDING,
        )

    def cancel_order(self, order_id: str) -> bool:
        """Remove one order from the open-order view."""
        self.cancelled.add(order_id)
        return True

    def get_position(self, ticker: str) -> Position | None:
        """Return one actual holding."""
        return self.positions.get(ticker)

    def get_positions(self) -> dict[str, int]:
        """Return all actual broker holdings."""
        return {ticker: position.quantity for ticker, position in self.positions.items()}

    def get_account_balance(self) -> AccountBalance:
        """Return fixed cash and marked position value."""
        value = sum(position.market_value for position in self.positions.values())
        return AccountBalance(20_000_000, value, 20_000_000 + value)

    def is_market_open(self) -> bool:
        """Keep service tests in the regular session."""
        return True

    def get_open_orders(self) -> list[PendingOrder]:
        """Return accepted orders that were not cancelled."""
        return [
            PendingOrder(
                order_id=order_id,
                ticker=order.ticker,
                side=order.side,
                quantity=order.quantity,
                remaining_quantity=order.quantity,
                order_price=order.limit_price,
            )
            for order_id, order in self.orders
            if order_id not in self.cancelled
        ]

    def get_daily_order_results(self) -> list[OrderResult]:
        """Return no fills until a test scripts an actual position."""
        return []


def _candidate() -> Candidate:
    return Candidate(
        ticker="005930",
        market="KOSPI",
        upper_limit_price=100_000,
        total_listed_shares=10_000_000,
        current_price=96_000,
        change_rate=25.0,
    )


def _service(db, mode: LimitUpMode, broker: ServiceBroker | None = None) -> LimitUpService:
    repo = LimitUpRepository(db)
    worker = None
    if broker is not None:
        manager = OrderManager(broker, RiskManager(broker, db), db)
        worker = LimitUpCommandWorker(manager, broker, repo)
    return LimitUpService(
        mode=mode,
        config=LimitUpConfig(),
        repository=repo,
        worker=worker,
    )


def _trade(at: float, price: int) -> FeedTrade:
    return FeedTrade(
        ticker="005930",
        price=price,
        cumulative_turnover_krw=50_000_000_000,
        execution_strength=151.0,
        buy_initiated=True,
        received_at=at,
    )


def test_automatic_trigger_submits_exactly_two_grid_orders(db) -> None:
    """One valid upward cross must produce one durable S/A net, never duplicates."""
    broker = ServiceBroker()
    service = _service(db, LimitUpMode.AUTOMATIC, broker)
    now = dt.datetime(2026, 8, 28, 10, 0, tzinfo=KST)
    assert service.watch_candidate(_candidate(), now_kst=now)

    service.on_trade(_trade(1.0, 99_600), now_kst=now)
    service.on_trade(_trade(2.0, 99_700), now_kst=now)
    service.on_trade(_trade(3.0, 99_800), now_kst=now)

    assert [order.strategy_id for _, order in broker.orders] == [
        "limit_up_v1:S",
        "limit_up_v1:A",
    ]
    assert service.status()["attempts"] == 1


def test_recommend_only_uses_same_fill_and_lock_state_machine_without_orders(db) -> None:
    """Shadow validation must exercise lifecycle logic without touching a broker."""
    service = _service(db, LimitUpMode.RECOMMEND_ONLY)
    now = dt.datetime(2026, 8, 28, 10, 0, tzinfo=KST)
    service.watch_candidate(_candidate(), now_kst=now)
    service.on_trade(_trade(1.0, 99_600), now_kst=now)
    service.on_trade(_trade(2.0, 99_700), now_kst=now)

    service.on_trade(_trade(3.0, 98_800), now_kst=now)
    service.on_quote(
        FeedQuote("005930", 100_000, 0, 100_000, 100_000, 4.0),
        now_kst=now,
    )
    service.tick(now_monotonic=14.0)

    assert service.machine("005930").state is LimitUpState.LOCKED
    assert service.status()["pattern_failures"] == 0


def test_kosdaq_halt_cancels_pending_grid_and_never_reopens(db) -> None:
    """The intraday market latch must pull buys and stay off after recovery."""
    broker = ServiceBroker()
    service = _service(db, LimitUpMode.AUTOMATIC, broker)
    now = dt.datetime(2026, 8, 28, 10, 0, tzinfo=KST)
    service.watch_candidate(_candidate(), now_kst=now)
    service.on_trade(_trade(1.0, 99_600), now_kst=now)
    service.on_trade(_trade(2.0, 99_700), now_kst=now)

    service.on_kosdaq(value=1_000.0, at=3.0)
    service.on_kosdaq(value=985.0, at=4.0)
    service.on_kosdaq(value=1_010.0, at=5.0)

    assert broker.cancelled == {"2001", "2002"}
    assert service.status()["entry_halted"] is True


def test_eod_review_holds_only_when_both_fresh_bid_tests_pass(db) -> None:
    """A locked recommendation may cross overnight only on a strict fresh PASS."""
    service = _service(db, LimitUpMode.RECOMMEND_ONLY)
    now = dt.datetime(2026, 8, 28, 10, 0, tzinfo=KST)
    service.watch_candidate(_candidate(), now_kst=now)
    machine = service.machine("005930")
    machine.fire_net(at=1.0)
    machine.on_fill(at=2.0, cumulative_quantity=1)
    machine.on_quote(
        event=service.quote_event("005930", price=100_000, ask_qty=0, at=3.0)
    )
    machine.on_timer(13.0)

    decision = service.review_eod(
        "005930",
        best_bid_price=100_000,
        best_bid_qty=100_000,
        quote_fresh=True,
        shares_fresh=True,
    )

    assert decision == "hold"
    assert machine.state is LimitUpState.OVERNIGHT


def test_recovery_unknown_broker_position_enters_manual_lock(db) -> None:
    """A position with no V1 session must never be auto-sold or silently adopted."""
    broker = ServiceBroker()
    broker.positions["000660"] = Position("000660", 3, 200_000)
    service = _service(db, LimitUpMode.AUTOMATIC, broker)

    service.recover(ref_date=dt.date(2026, 8, 28), now_monotonic=100.0)

    assert service.status()["manual_lock"] is True
    assert service.status()["unknown_positions"] == ["000660"]
    assert broker.orders == []


def test_recovery_restores_first_fill_deadline_and_eod_share_facts(db) -> None:
    """A restart must preserve the original 180-second clock and EOD inputs."""
    broker = ServiceBroker()
    service = _service(db, LimitUpMode.AUTOMATIC, broker)
    now = dt.datetime(2026, 8, 28, 10, 0, tzinfo=KST)
    service.watch_candidate(_candidate(), now_kst=now)
    row = service._sessions["005930"]
    row.state = LimitUpState.FILLED_WAIT_LOCK.value
    row.first_fill_at = dt.datetime(2026, 8, 28, 1, 0, 0)
    service.repository.db.commit()
    broker.positions["005930"] = Position("005930", 3, 98_800)

    restarted = _service(db, LimitUpMode.AUTOMATIC, broker)
    restarted.recover(
        ref_date=dt.date(2026, 8, 28),
        now_monotonic=1_000.0,
        now_kst=dt.datetime(2026, 8, 28, 10, 2, 0, tzinfo=KST),
    )

    machine = restarted.machine("005930")
    assert machine.first_fill_at == 880.0
    assert restarted._candidates["005930"].total_listed_shares == 10_000_000


def test_feed_disconnect_pulls_unfilled_net_but_keeps_held_position_protected(db) -> None:
    """Loss of real-time truth must close entries without dumping a refuge leader."""
    broker = ServiceBroker()
    service = _service(db, LimitUpMode.AUTOMATIC, broker)
    now = dt.datetime(2026, 8, 28, 10, 0, tzinfo=KST)
    service.watch_candidate(_candidate(), now_kst=now)
    service.on_trade(_trade(1.0, 99_600), now_kst=now)
    service.on_trade(_trade(2.0, 99_700), now_kst=now)

    service.on_feed_disconnect(at=3.0, now_kst=now)

    assert broker.cancelled == {"2001", "2002"}
    assert "feed_disconnected" in service.status()["halted_reasons"]

    held_broker = ServiceBroker()
    row = service._sessions["005930"]
    row.state = LimitUpState.LOCKED.value
    service.repository.db.commit()
    held_broker.positions["005930"] = Position("005930", 3, 98_800)
    held = _service(db, LimitUpMode.AUTOMATIC, held_broker)
    held.recover(ref_date=now.date(), now_monotonic=10.0, now_kst=now)
    held.on_feed_disconnect(at=11.0, now_kst=now)

    assert held_broker.orders == []


def test_rest_fallback_price_can_fire_hard_stop_but_never_entry(db) -> None:
    """REST degradation mode is protection-only and may still liquidate a break."""
    broker = ServiceBroker()
    service = _service(db, LimitUpMode.AUTOMATIC, broker)
    now = dt.datetime(2026, 8, 28, 10, 0, tzinfo=KST)
    service.watch_candidate(_candidate(), now_kst=now)
    machine = service.machine("005930")
    machine.state = LimitUpState.LOCKED
    machine.filled_quantity = 3
    broker.positions["005930"] = Position("005930", 3, 98_800)

    service.on_fallback_price("005930", price=94_900, at=20.0, now_kst=now)

    assert broker.orders[-1][1].side is OrderSide.SELL
    assert service.status()["pattern_failures"] == 1


def test_realized_daily_loss_limit_blocks_new_entries(db) -> None:
    """The confirmed -300,000 KRW stop must latch from persisted P/L, not memory.

    Regression: DailyGuard.update_daily_pnl had no caller, so the only
    money-based guard was dead while the count-based ones ran.
    """
    broker = ServiceBroker()
    service = _service(db, LimitUpMode.AUTOMATIC, broker)
    now = dt.datetime(2026, 8, 28, 10, 0, tzinfo=KST)
    closed = service.repository.create_or_get_session(
        ref_date=now.date(),
        ticker="000660",
        market="KOSPI",
        upper_limit_price=50_000,
        trigger_price=49_850,
    )
    closed.realized_pnl = -300_000.0
    db.commit()

    service.watch_candidate(_candidate(), now_kst=now)
    service.on_trade(_trade(1.0, 99_600), now_kst=now)
    service.on_trade(_trade(2.0, 99_700), now_kst=now)
    service.on_trade(_trade(3.0, 99_800), now_kst=now)

    assert broker.orders == []
    assert service.machine("005930").state is LimitUpState.CLOSED
    status = service.status()
    assert status["daily_pnl"] == -300_000.0
    assert "daily_loss" in status["halted_reasons"]


def test_profitable_day_does_not_latch_the_daily_loss_stop(db) -> None:
    """Only realized losses close the gate; a green day must still trade."""
    broker = ServiceBroker()
    service = _service(db, LimitUpMode.AUTOMATIC, broker)
    now = dt.datetime(2026, 8, 28, 10, 0, tzinfo=KST)
    won = service.repository.create_or_get_session(
        ref_date=now.date(),
        ticker="000660",
        market="KOSPI",
        upper_limit_price=50_000,
        trigger_price=49_850,
    )
    won.realized_pnl = 400_000.0
    db.commit()

    service.watch_candidate(_candidate(), now_kst=now)
    service.on_trade(_trade(1.0, 99_600), now_kst=now)
    service.on_trade(_trade(2.0, 99_700), now_kst=now)
    service.on_trade(_trade(3.0, 99_800), now_kst=now)

    assert len(broker.orders) == 2
    assert service.status()["daily_pnl"] == 400_000.0


def _overnight_service(db, held: int) -> tuple[ServiceBroker, LimitUpService]:
    """Drive one session to a locked overnight hold with `held` shares."""
    broker = ServiceBroker()
    service = _service(db, LimitUpMode.AUTOMATIC, broker)
    now = dt.datetime(2026, 8, 28, 10, 0, tzinfo=KST)
    service.watch_candidate(_candidate(), now_kst=now)
    broker.positions["005930"] = Position("005930", held, 98_000.0)
    machine = service.machine("005930")
    machine.fire_net(at=1.0)
    machine.on_fill(at=2.0, cumulative_quantity=held)
    machine.on_quote(
        event=service.quote_event("005930", price=100_000, ask_qty=0, at=3.0)
    )
    machine.on_timer(13.0)
    service.review_eod(
        "005930",
        best_bid_price=100_000,
        best_bid_qty=100_000,
        quote_fresh=True,
        shares_fresh=True,
    )
    assert machine.state is LimitUpState.OVERNIGHT
    return broker, service


def test_overnight_cap_trims_the_excess_at_the_upper_limit_price(db) -> None:
    """40 shares x 100,000 = 4,000,000 exposure would breach the -1,000,000 cap."""
    broker, service = _overnight_service(db, held=40)

    submitted = service.apply_overnight_cap(ref_date=dt.date(2026, 8, 28))

    # budget 3,333,333 / 100,000 = 33 shares allowed, 7 must go
    assert submitted == {"005930": 7}
    assert service.machine("005930").state is LimitUpState.EOD_TRIM
    _, sell = broker.orders[-1]
    assert (sell.side, sell.quantity, sell.limit_price) == (OrderSide.SELL, 7, 100_000)
    assert 33 * 100_000 * 0.30 <= 1_000_000


def test_position_inside_the_budget_is_never_trimmed(db) -> None:
    """The cap must not touch a carry that is already small enough."""
    broker, service = _overnight_service(db, held=30)

    assert service.apply_overnight_cap(ref_date=dt.date(2026, 8, 28)) == {}
    assert service.machine("005930").state is LimitUpState.OVERNIGHT
    assert all(order.side is OrderSide.BUY for _, order in broker.orders)


def test_realized_loss_shrinks_the_overnight_carry(db) -> None:
    """A -300,000 KRW day leaves only 2,333,333 KRW of overnight room."""
    broker, service = _overnight_service(db, held=40)
    spent = service.repository.create_or_get_session(
        ref_date=dt.date(2026, 8, 28),
        ticker="000660",
        market="KOSPI",
        upper_limit_price=50_000,
        trigger_price=49_850,
    )
    spent.realized_pnl = -300_000.0
    db.commit()

    submitted = service.apply_overnight_cap(ref_date=dt.date(2026, 8, 28))

    # 2,333,333 / 100,000 = 23 shares; the loss already spent 300,000 of the cap
    assert submitted == {"005930": 17}
    assert 300_000 + 23 * 100_000 * 0.30 <= 1_000_000


def test_filled_trim_returns_the_session_to_overnight(db) -> None:
    """Once the excess is gone the session may cross the night as planned."""
    broker, service = _overnight_service(db, held=40)
    service.apply_overnight_cap(ref_date=dt.date(2026, 8, 28))
    broker.positions["005930"] = Position("005930", 33, 98_000.0)

    restored = service.confirm_overnight_cap(ref_date=dt.date(2026, 8, 28))

    assert restored == ["005930"]
    assert service.machine("005930").state is LimitUpState.OVERNIGHT


def test_unfilled_trim_gives_up_the_carry_instead_of_breaching_the_cap(db) -> None:
    """Fail closed: an untrimmable position must not go overnight at all."""
    broker, service = _overnight_service(db, held=40)
    service.apply_overnight_cap(ref_date=dt.date(2026, 8, 28))
    trim_order_id = broker.orders[-1][0]

    assert service.confirm_overnight_cap(ref_date=dt.date(2026, 8, 28)) == []
    liquidated = service.force_overnight_cap(ref_date=dt.date(2026, 8, 28))

    assert liquidated == ["005930"]
    assert trim_order_id in broker.cancelled
    _, final = broker.orders[-1]
    assert (final.side, final.quantity) == (OrderSide.SELL, 40)
    assert service._sessions["005930"].end_reason == "overnight_cap_unfilled"
