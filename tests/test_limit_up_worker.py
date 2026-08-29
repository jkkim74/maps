"""Order-worker race handling and broker-authoritative reconciliation."""

from __future__ import annotations

import datetime as dt

import pytest

from maps.common.exceptions import BrokerAdapterError
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
from maps.limit_up.domain import build_grid
from maps.limit_up.repository import LimitUpRepository
from maps.limit_up.worker import LimitUpCommandWorker
from maps.risk.manager import RiskManager


class ScriptedBroker(BrokerAdapter):
    """Deterministic broker boundary used to exercise the real worker and manager."""

    def __init__(self) -> None:
        """Create a pending-order broker with optional scripted failures."""
        self.orders: list[Order] = []
        self.cancelled: list[str] = []
        self.fail_buy_number: int | None = None
        self.cancel_error = False
        self.daily_results: list[OrderResult] = []
        self.open_orders: list[PendingOrder] = []
        self.position: Position | None = None

    def place_order(self, order: Order) -> OrderResult:
        """Record an order or raise on the selected buy sequence number."""
        buy_number = sum(item.side is OrderSide.BUY for item in self.orders) + 1
        if order.side is OrderSide.BUY and self.fail_buy_number == buy_number:
            raise BrokerAdapterError("scripted second-leg rejection")
        self.orders.append(order)
        return OrderResult(
            order_id=str(1000 + len(self.orders)),
            strategy_id=order.strategy_id,
            ticker=order.ticker,
            side=order.side,
            status=OrderStatus.PENDING,
            submitted_at=dt.datetime(2026, 8, 28, 10, 0, len(self.orders)),
        )

    def cancel_order(self, order_id: str) -> bool:
        """Record a cancellation or emulate the fill/cancel race error."""
        if self.cancel_error:
            raise BrokerAdapterError("already filled")
        self.cancelled.append(order_id)
        return True

    def get_position(self, ticker: str) -> Position | None:
        """Return the scripted actual holding."""
        return self.position if self.position and self.position.ticker == ticker else None

    def get_account_balance(self) -> AccountBalance:
        """Return the fixed V1 test account."""
        position_value = self.position.market_value if self.position else 0.0
        return AccountBalance(cash=20_000_000, positions_value=position_value, total_assets=20_000_000)

    def is_market_open(self) -> bool:
        """Keep the deterministic broker open."""
        return True

    def get_open_orders(self) -> list[PendingOrder]:
        """Return scripted broker open orders."""
        return self.open_orders

    def get_daily_order_results(self) -> list[OrderResult]:
        """Return scripted daily fill evidence."""
        return self.daily_results


def _worker(db, broker: ScriptedBroker) -> tuple[LimitUpCommandWorker, object]:
    repo = LimitUpRepository(db)
    session = repo.create_or_get_session(
        ref_date=dt.date(2026, 8, 28),
        ticker="005930",
        market="KOSPI",
        upper_limit_price=100_000,
        trigger_price=99_700,
    )
    manager = OrderManager(broker, RiskManager(broker, db), db)
    return LimitUpCommandWorker(manager, broker, repo), session


def test_grid_submits_s_then_a_and_persists_broker_ids(db) -> None:
    """Reversing or losing one leg would break fixed 60/40 recovery."""
    broker = ScriptedBroker()
    worker, session = _worker(db, broker)

    result = worker.fire_grid(session, build_grid(upper_limit_price=100_000, budget_krw=2_000_000))

    assert result.position_quantity == 0
    assert [order.strategy_id for order in broker.orders] == ["limit_up_v1:S", "limit_up_v1:A"]
    assert [(leg.name, leg.broker_order_id, leg.status) for leg in worker.legs(session)] == [
        ("A", "1002", "pending"),
        ("S", "1001", "pending"),
    ]


def test_second_leg_failure_cancels_first_and_reconciles_instead_of_crashing(db) -> None:
    """A half-open grid must be withdrawn before the worker accepts more work."""
    broker = ScriptedBroker()
    broker.fail_buy_number = 2
    worker, session = _worker(db, broker)

    result = worker.fire_grid(session, build_grid(upper_limit_price=100_000, budget_krw=2_000_000))

    assert broker.cancelled == ["1001"]
    assert result.position_quantity == 0
    assert worker.legs(session)[1].status == "cancelled"


def test_cancel_error_uses_fills_then_open_orders_then_position(db) -> None:
    """An 'already filled' error must be reconciled, never guessed as a fill."""
    broker = ScriptedBroker()
    worker, session = _worker(db, broker)
    worker.fire_grid(session, build_grid(upper_limit_price=100_000, budget_krw=2_000_000))
    broker.cancel_error = True
    broker.daily_results = [
        OrderResult(
            order_id="1001",
            strategy_id="",
            ticker="005930",
            side=OrderSide.BUY,
            status=OrderStatus.FILLED,
            filled_quantity=12,
            avg_price=98_800,
        )
    ]
    broker.open_orders = [
        PendingOrder(
            order_id="1002",
            ticker="005930",
            side=OrderSide.BUY,
            quantity=8,
            remaining_quantity=8,
        )
    ]
    broker.position = Position("005930", quantity=12, avg_price=98_800)

    result = worker.cancel_pending_buys(session)

    assert result.position_quantity == 12
    assert result.open_order_ids == ("1002",)
    assert [(leg.name, leg.filled_quantity, leg.status) for leg in worker.legs(session)] == [
        ("A", 0, "pending"),
        ("S", 12, "filled"),
    ]


def test_restart_never_resubmits_ambiguous_persisted_buy_intent(db) -> None:
    """A crash after durable intent but before order ID must fail closed."""
    broker = ScriptedBroker()
    worker, session = _worker(db, broker)
    grid = build_grid(upper_limit_price=100_000, budget_krw=2_000_000)
    for leg in grid:
        worker.repository.upsert_leg(
            session, name=leg.name, price=leg.price, quantity=leg.quantity
        )
    worker.repository.append_event(
        session,
        action="submit_buy",
        state_version=session.state_version,
        leg="S",
        payload={"price": grid[0].price, "quantity": grid[0].quantity},
    )
    worker.repository.db.commit()

    result = worker.fire_grid(session, grid)

    assert result.position_quantity == 0
    assert broker.orders == []
    assert worker._leg(session, "S").status == "reconciling"


def _filled_grid(db, broker: ScriptedBroker):
    """Fire the fixed grid and mark both legs fully filled at their limit prices."""
    worker, session = _worker(db, broker)
    worker.fire_grid(session, build_grid(upper_limit_price=100_000, budget_krw=2_000_000))
    broker.daily_results = [
        OrderResult(
            order_id="1001",
            strategy_id="limit_up_v1:S",
            ticker="005930",
            side=OrderSide.BUY,
            status=OrderStatus.FILLED,
            filled_quantity=12,
            avg_price=98_800.0,
        ),
        OrderResult(
            order_id="1002",
            strategy_id="limit_up_v1:A",
            ticker="005930",
            side=OrderSide.BUY,
            status=OrderStatus.FILLED,
            filled_quantity=8,
            avg_price=97_500.0,
        ),
    ]
    broker.position = Position(
        ticker="005930", quantity=20, avg_price=98_280.0, current_price=92_000.0
    )
    return worker, session


def test_exit_fill_settles_session_realized_pnl(db) -> None:
    """The daily loss latch is rebuilt from this column; an unpriced exit disarms it."""
    broker = ScriptedBroker()
    worker, session = _filled_grid(db, broker)
    broker.daily_results.append(
        OrderResult(
            order_id="1003",
            strategy_id="limit_up_v1:exit",
            ticker="005930",
            side=OrderSide.SELL,
            status=OrderStatus.FILLED,
            filled_quantity=20,
            avg_price=92_000.0,
        )
    )

    worker.sell_actual_position(session, reason="hard_stop")

    # buy 1,965,600 / sell 1,840,000 / fee 570.84 / sell tax 3,312
    assert session.realized_pnl == pytest.approx(-129_482.84)


def test_unfilled_exit_leaves_realized_pnl_unknown_rather_than_zero(db) -> None:
    """Booking an unfilled exit as 0 would silently loosen the daily loss limit."""
    broker = ScriptedBroker()
    worker, session = _filled_grid(db, broker)

    worker.sell_actual_position(session, reason="hard_stop")

    assert session.exit_order_ids is not None
    assert session.realized_pnl is None
