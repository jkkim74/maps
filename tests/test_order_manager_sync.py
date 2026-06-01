"""OrderManager live-order safety tests."""

from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock

import pytest

from maps.common.exceptions import BrokerAdapterError, DuplicateOrderError
from maps.common.models import OrderLog
from maps.execution.broker_adapter import AccountBalance, Order, OrderResult, OrderSide, OrderStatus, OrderType
from maps.execution.mock_broker import MockBroker
from maps.execution.order_manager import OrderManager
from maps.risk.manager import RiskConfig, RiskManager


def _buy(strategy: str = "live_strat") -> Order:
    return Order(
        strategy_id=strategy,
        ticker="AAAA",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=10,
        limit_price=10_000,
    )


def test_duplicate_active_order_blocked(db) -> None:
    broker = MockBroker(initial_cash=10_000_000, price_feed={"AAAA": 10_000})
    risk = RiskManager(broker=broker, db=db, config=RiskConfig())
    manager = OrderManager(broker=broker, risk=risk, db=db)

    manager.submit(_buy(strategy="dup"))

    with pytest.raises(DuplicateOrderError):
        manager.submit(_buy(strategy="dup"))


def test_sync_broker_state_updates_fill_status(db) -> None:
    broker = MagicMock()
    broker.get_account_balance.return_value = AccountBalance(cash=1_000_000, positions_value=500_000)
    broker.get_open_orders.return_value = []
    broker.get_daily_order_results.return_value = [
        OrderResult(
            order_id="o1",
            strategy_id="s1",
            ticker="AAAA",
            side=OrderSide.BUY,
            status=OrderStatus.FILLED,
            filled_quantity=10,
            avg_price=10_100,
        )
    ]
    risk = RiskManager(broker=broker, db=db, config=RiskConfig())
    manager = OrderManager(broker=broker, risk=risk, db=db)
    db.add(
        OrderLog(
            order_id="o1",
            strategy_id="s1",
            ticker="AAAA",
            side=OrderSide.BUY.value,
            qty=10,
            order_price=10_000,
            fill_qty=0,
            status=OrderStatus.PENDING.value,
            broker="kis",
            mode="mock",
        )
    )
    db.commit()

    result = manager.sync_broker_state()

    row = db.query(OrderLog).filter(OrderLog.order_id == "o1").one()
    assert result["updated_orders"] == 1
    assert row.status == OrderStatus.FILLED.value
    assert row.fill_qty == 10
    assert row.fill_price == 10_100


def test_sync_broker_state_tolerates_daily_fill_lookup_error(db) -> None:
    broker = MagicMock()
    broker.get_account_balance.return_value = AccountBalance(cash=1_000_000, positions_value=500_000)
    broker.get_open_orders.return_value = []
    broker.get_daily_order_results.side_effect = BrokerAdapterError("KIS request failed after 3 attempts")
    risk = RiskManager(broker=broker, db=db, config=RiskConfig())
    manager = OrderManager(broker=broker, risk=risk, db=db)

    result = manager.sync_broker_state()

    assert result["cash"] == 1_000_000
    assert result["positions_value"] == 500_000
    assert result["open_orders"] == 0
    assert result["updated_orders"] == 0
    assert result["sync_errors"] == 1


def test_sync_broker_state_does_not_infer_fill_from_position(db) -> None:
    broker = MagicMock()
    broker.get_account_balance.return_value = AccountBalance(cash=1_000_000, positions_value=100_000)
    broker.get_open_orders.return_value = []
    broker.get_daily_order_results.return_value = []
    broker.get_positions.return_value = {"AAAA": 10}
    risk = RiskManager(broker=broker, db=db, config=RiskConfig())
    manager = OrderManager(broker=broker, risk=risk, db=db)
    db.add(
        OrderLog(
            order_id="o-position",
            strategy_id="s1",
            ticker="AAAA",
            side=OrderSide.BUY.value,
            qty=10,
            order_price=10_000,
            fill_qty=0,
            status=OrderStatus.PENDING.value,
            broker="kis",
            mode="mock",
            created_at=dt.datetime.now(),
        )
    )
    db.commit()

    result = manager.sync_broker_state()

    row = db.query(OrderLog).filter(OrderLog.order_id == "o-position").one()
    assert result["updated_orders"] == 0
    assert row.status == OrderStatus.PENDING.value
    assert row.fill_qty == 0


def test_transient_broker_error_is_retried(db) -> None:
    broker = MagicMock()
    broker.get_account_balance.return_value = AccountBalance(cash=10_000_000, positions_value=0)
    broker.place_order.side_effect = [
        BrokerAdapterError("KIS transient HTTP 503"),
        OrderResult(
            order_id="retry-1",
            strategy_id="retry",
            ticker="AAAA",
            side=OrderSide.BUY,
            status=OrderStatus.PENDING,
        ),
    ]
    risk = RiskManager(broker=broker, db=db, config=RiskConfig())
    manager = OrderManager(broker=broker, risk=risk, db=db)

    result = manager.submit(_buy(strategy="retry"))

    assert result.order_id == "retry-1"
    assert broker.place_order.call_count == 2
