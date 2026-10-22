"""A cancelled-on-gap buy remains recoverable until broker truth is terminal."""

import datetime as dt
from dataclasses import replace
from decimal import Decimal

import pytest

from maps.common.exceptions import BrokerAdapterError
from maps.execution.broker_adapter import AccountBalance, OrderSide, OrderStatus, Position
from maps.limit_up.domain import LimitUpState
from maps.limit_up.repository import LimitUpRepository
from maps.limit_up.service import LimitUpMode, LimitUpService
from tests.test_limit_up_service import KST, ServiceBroker, _candidate, _service, _trade


class GapBroker(ServiceBroker):
    """Expose confirmed fills and independently scripted cancellation failures."""

    def __init__(self):
        super().__init__()
        self.cancel_outcome = True
        self.fail_open_orders = False
        self.fills = {}

    def cancel_order(self, order_id):
        if isinstance(self.cancel_outcome, Exception):
            raise self.cancel_outcome
        return super().cancel_order(order_id) if self.cancel_outcome else False

    def get_open_orders(self):
        if self.fail_open_orders:
            raise BrokerAdapterError("synthetic reconciliation unavailable")
        return [replace(order, remaining_quantity=order.quantity - self.fills.get(order.order_id, 0))
                for order in super().get_open_orders()
                if self.fills.get(order.order_id, 0) < order.quantity]

    def get_daily_order_results(self):
        results = []
        for result in super().get_daily_order_results():
            quantity = self.fills.get(result.order_id, 0)
            request = next(request for oid, request in self.orders if oid == result.order_id)
            if quantity:
                price = request.limit_price or 90_000
                result = replace(result,
                    status=OrderStatus.FILLED if quantity == request.quantity else OrderStatus.PARTIALLY_FILLED,
                    filled_quantity=quantity, avg_price=price,
                    cumulative_gross=Decimal(str(price)) * quantity,
                    tax=0, costs_complete=True,
                    filled_at=dt.datetime.now())
            results.append(result)
        return results

    def get_account_balance(self):
        spent = sum(self.fills.get(oid, 0) * (request.limit_price or 90_000)
                    * (1 if request.side is OrderSide.BUY else -1) for oid, request in self.orders)
        value = sum(position.market_value for position in self.positions.values())
        return AccountBalance(20_000_000 - spent, value, 20_000_000 - spent + value)

    def fill_first_buy(self):
        oid, request = self.orders[0]
        self.fills[oid] = request.quantity
        self.positions[request.ticker] = Position(request.ticker, request.quantity, request.limit_price,
                                                  current_price=request.limit_price)
        return request.quantity


def open_grid(db):
    """Enter through the real service/worker/OrderManager synthetic broker path."""
    broker = GapBroker()
    service = _service(db, LimitUpMode.AUTOMATIC, broker)
    wall = dt.datetime(2026, 8, 28, 10, tzinfo=KST)
    service.watch_candidate(_candidate(), now_kst=wall)
    service.on_trade(_trade(1.0, 99_600), now_kst=wall)
    service.on_trade(_trade(2.0, 99_700), now_kst=wall)
    assert len(broker.orders) == 2
    return broker, service, wall


@pytest.mark.parametrize("failure", ["false", "cancel_exception", "reconcile_exception"])
@pytest.mark.parametrize("restart", [False, True])
def test_gap_cancel_failure_keeps_late_fill_under_protection(db, failure, restart):
    """An unresolved cancel reserves a slot and later broker-owned shares regain protection."""
    broker, service, wall = open_grid(db)
    broker.cancel_outcome = BrokerAdapterError("synthetic cancel failed") if failure == "cancel_exception" else False
    broker.fail_open_orders = failure == "reconcile_exception"
    service.on_feed_gap("005930", now_kst=wall)
    session = service._sessions["005930"]
    db.refresh(session)
    assert service.manual_lock
    assert service.machine("005930").state is LimitUpState.RECONCILING
    assert session.state == LimitUpState.RECONCILING.value
    assert service.machine("005930").filled_quantity == 0
    assert service._active_count() == 1

    broker.fail_open_orders = False
    confirmed = broker.fill_first_buy()
    if restart:
        service = LimitUpService(mode=LimitUpMode.AUTOMATIC, config=service.config,
            repository=LimitUpRepository(db), worker=service.worker)
        service.recover(ref_date=wall.date(), now_monotonic=10, now_kst=wall)
        assert service.unknown_positions == []
    service.tick(now_monotonic=11, now_kst=wall)
    assert service.machine("005930").filled_quantity == confirmed
    assert service.machine("005930").state is LimitUpState.FILLED_WAIT_LOCK
    assert service.held_tickers() == ["005930"]
    assert service._active_count() == 1 and service.manual_lock
    assert [request for _, request in broker.orders if request.side is OrderSide.SELL] == []

    # Cancellation eventually becomes clear, while the confirmed holding remains protected.
    broker.cancel_outcome = True
    service.tick(now_monotonic=12, now_kst=wall)
    assert service.machine("005930").state is LimitUpState.FILLED_WAIT_LOCK
    assert service.machine("005930").filled_quantity == confirmed
    assert broker.get_open_orders() == []
    assert [request for _, request in broker.orders if request.side is OrderSide.SELL] == []


@pytest.mark.parametrize("reason", ["feed_gap", "market_halt", "no_fill_timeout"])
def test_pending_cancel_without_fill_closes_only_after_verified_clear(db, reason):
    """The shared close-then-cancel paths cannot abandon an unresolved live buy."""
    broker, service, wall = open_grid(db)
    broker.cancel_outcome = False
    if reason == "feed_gap":
        service.on_feed_gap("005930", now_kst=wall)
    elif reason == "market_halt":
        service.on_kosdaq(value=1000, at=3, now_kst=wall)
        service.on_kosdaq(value=980, at=4, now_kst=wall)
    else:
        service.tick(now_monotonic=200, now_kst=wall)
    assert service.machine("005930").state is LimitUpState.RECONCILING
    assert service._active_count() == 1
    broker.cancel_outcome = True
    service.tick(now_monotonic=201, now_kst=wall)
    assert service.machine("005930").state is LimitUpState.CLOSED
    assert service._sessions["005930"].state == LimitUpState.CLOSED.value
    assert service._sessions["005930"].end_reason == reason
    assert service._active_count() == 0
    assert broker.get_open_orders() == []
    assert service.held_tickers() == []


def test_successful_gap_cancellation_closes_without_false_holdings(db):
    """A directly confirmed cancellation preserves the existing no-fill close."""
    broker, service, wall = open_grid(db)
    service.on_feed_gap("005930", now_kst=wall)
    assert service.machine("005930").state is LimitUpState.CLOSED
    assert service.machine("005930").filled_quantity == 0
    assert service._sessions["005930"].end_reason == "feed_gap"
    assert broker.get_open_orders() == [] and service._active_count() == 0
    assert not service.manual_lock


def test_clear_cancel_and_first_late_fill_in_same_tick_cannot_close_position(db):
    """Flush new leg fills before using aggregate ownership to decide closure."""
    broker, service, wall = open_grid(db)
    broker.cancel_outcome = False
    service.on_feed_gap("005930", now_kst=wall)
    confirmed = broker.fill_first_buy()
    broker.cancel_outcome = True
    service.tick(now_monotonic=11, now_kst=wall)
    assert service.machine("005930").filled_quantity == confirmed
    assert service.machine("005930").state is LimitUpState.FILLED_WAIT_LOCK
    assert service.held_tickers() == ["005930"]
    assert broker.get_open_orders() == []


@pytest.mark.parametrize("stop", ["hard_stop", "time_stop"])
@pytest.mark.parametrize("restart", [False, True])
def test_pending_buy_cancel_retains_protective_exit_until_delayed_sell_finishes(db, stop, restart):
    """Buy cleanup must never turn an authorized asynchronous exit back into a held state."""
    broker, service, wall = open_grid(db)
    broker.cancel_outcome = False
    service.on_feed_gap("005930", now_kst=wall)
    broker.fill_first_buy()
    service.tick(now_monotonic=11, now_kst=wall)
    service.tick(now_monotonic=12, now_kst=wall)
    assert service.machine("005930").filled_quantity > 0
    if stop == "hard_stop":
        service.on_trade(_trade(13, 90_000), now_kst=wall)
    else:
        service.tick(now_monotonic=1000, now_kst=wall)
    assert service.machine("005930").state is LimitUpState.RECONCILING
    assert [request for _, request in broker.orders if request.side is OrderSide.SELL] == []
    if restart:
        service = LimitUpService(mode=LimitUpMode.AUTOMATIC, config=service.config,
            repository=LimitUpRepository(db), worker=service.worker)
        service.recover(ref_date=wall.date(), now_monotonic=1000, now_kst=wall)
        assert service.machine("005930").state is LimitUpState.RECONCILING
        assert [request for _, request in broker.orders if request.side is OrderSide.SELL] == []
    broker.cancel_outcome = True
    service.tick(now_monotonic=1001, now_kst=wall)
    service.tick(now_monotonic=1002, now_kst=wall)
    exits = [(oid, request) for oid, request in broker.orders if request.side is OrderSide.SELL]
    assert len(exits) == 1
    assert service.machine("005930").state is LimitUpState.RECONCILING
    oid, request = exits[0]
    broker.fills[oid] = request.quantity
    broker.positions.pop("005930")
    service.tick(now_monotonic=1003, now_kst=wall)
    assert service.machine("005930").state is LimitUpState.CLOSED
    assert service._active_count() == 0
