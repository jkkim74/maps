"""OrderManager live-order safety tests."""

from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock

import pytest

from maps.common.exceptions import BrokerAdapterError, DuplicateOrderError
from maps.common.models import OrderLog
from maps.common.settings import MapsSettings
from maps.execution.broker_adapter import (
    AccountBalance,
    Order,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
    SameDayBuy,
    order_log_id,
)
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


def test_sync_reused_kis_order_id_does_not_update_prior_day_ticker(
    db,
    monkeypatch,
) -> None:
    """다른 거래일에 재사용된 ODNO가 과거 다른 종목 행을 덮으면 안 된다."""
    settings = MapsSettings(
        maps_broker_mode="kis",
        kis_account_no="11111111-01",
    )
    monkeypatch.setattr("maps.execution.order_manager.get_settings", lambda: settings)
    broker = MagicMock()
    broker.get_account_balance.return_value = AccountBalance(
        cash=1_000_000,
        positions_value=500_000,
    )
    broker.get_open_orders.return_value = []
    broker.get_daily_order_results.return_value = [
        OrderResult(
            order_id="0000000755",
            strategy_id="",
            ticker="041830",
            side=OrderSide.BUY,
            status=OrderStatus.FILLED,
            filled_quantity=35,
            avg_price=69_200,
            submitted_at=dt.datetime(2026, 8, 10, 8, 55),
        )
    ]
    old = OrderLog(
        order_id="0000000755",
        strategy_id="ath_breakout_v1",
        ticker="051160",
        side=OrderSide.BUY.value,
        qty=427,
        order_price=57_200,
        fill_qty=0,
        status="expired",
        broker="kis",
        mode="mock",
        created_at=dt.datetime(2026, 8, 5, 23, 55),
    )
    db.add(old)
    db.commit()
    manager = OrderManager(
        broker=broker,
        risk=RiskManager(broker=broker, db=db, config=RiskConfig()),
        db=db,
    )

    manager.sync_broker_state()

    db.refresh(old)
    current = db.query(OrderLog).filter(OrderLog.ticker == "041830").one()
    assert (old.status, old.fill_qty, old.fill_price) == ("expired", 0, None)
    assert current.order_id.endswith(":20260810:0000000755")
    assert (current.status, current.fill_qty, current.fill_price) == ("filled", 35, 69_200)


def test_sync_refuses_current_identity_with_ticker_mismatch(db, monkeypatch) -> None:
    """내부 ID가 같아도 종목이 다르면 손상된 행을 갱신하지 않는다."""
    settings = MapsSettings(
        maps_broker_mode="kis",
        kis_account_no="11111111-01",
    )
    monkeypatch.setattr("maps.execution.order_manager.get_settings", lambda: settings)
    submitted_at = dt.datetime(2026, 8, 10, 8, 55)
    internal_id = order_log_id(
        "0000000755",
        broker="kis",
        account_no=settings.kis_account_no,
        submitted_at=submitted_at,
    )
    broker = MagicMock()
    broker.get_account_balance.return_value = AccountBalance(
        cash=1_000_000,
        positions_value=500_000,
    )
    broker.get_open_orders.return_value = []
    broker.get_daily_order_results.return_value = [
        OrderResult(
            order_id="0000000755",
            strategy_id="",
            ticker="041830",
            side=OrderSide.BUY,
            status=OrderStatus.FILLED,
            filled_quantity=35,
            avg_price=69_200,
            submitted_at=submitted_at,
        )
    ]
    damaged = OrderLog(
        order_id=internal_id,
        strategy_id="ath_breakout_v1",
        ticker="051160",
        side=OrderSide.BUY.value,
        qty=427,
        order_price=57_200,
        fill_qty=0,
        status="filled",
        broker="kis",
        mode="mock",
        created_at=dt.datetime(2026, 8, 9, 23, 55),
    )
    db.add(damaged)
    db.commit()
    manager = OrderManager(
        broker=broker,
        risk=RiskManager(broker=broker, db=db, config=RiskConfig()),
        db=db,
    )

    result = manager.sync_broker_state()

    db.refresh(damaged)
    assert result["updated_orders"] == 0
    assert result["sync_errors"] == 1
    assert (damaged.status, damaged.fill_qty, damaged.fill_price) == ("filled", 0, None)


def test_sync_reused_kis_order_id_does_not_update_other_broker(db, monkeypatch) -> None:
    """같은 날·종목·방향이어도 다른 브로커의 raw ID 행은 KIS가 갱신하지 않는다."""
    settings = MapsSettings(
        maps_broker_mode="kis",
        kis_account_no="11111111-01",
    )
    monkeypatch.setattr("maps.execution.order_manager.get_settings", lambda: settings)
    submitted_at = dt.datetime(2026, 8, 10, 8, 55)
    broker = MagicMock()
    broker.get_account_balance.return_value = AccountBalance(
        cash=1_000_000,
        positions_value=500_000,
    )
    broker.get_open_orders.return_value = []
    broker.get_daily_order_results.return_value = [
        OrderResult(
            order_id="0000000755",
            strategy_id="",
            ticker="041830",
            side=OrderSide.BUY,
            status=OrderStatus.FILLED,
            filled_quantity=35,
            avg_price=69_200,
            submitted_at=submitted_at,
        )
    ]
    other_broker = OrderLog(
        order_id="0000000755",
        strategy_id="other_strategy",
        ticker="041830",
        side=OrderSide.BUY.value,
        qty=35,
        order_price=71_600,
        fill_qty=0,
        status="expired",
        broker="kiwoom",
        mode="mock",
        created_at=dt.datetime(2026, 8, 9, 23, 55),
    )
    db.add(other_broker)
    db.commit()
    manager = OrderManager(
        broker=broker,
        risk=RiskManager(broker=broker, db=db, config=RiskConfig()),
        db=db,
    )

    manager.sync_broker_state()

    db.refresh(other_broker)
    kis_row = db.query(OrderLog).filter(OrderLog.broker == "kis").one()
    assert (other_broker.status, other_broker.fill_qty) == ("expired", 0)
    assert kis_row.order_id.endswith(":20260810:0000000755")
    assert (kis_row.status, kis_row.fill_qty) == ("filled", 35)


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


def test_sync_broker_state_reconciles_explicit_same_day_buy(db) -> None:
    broker = MagicMock()
    broker.get_account_balance.return_value = AccountBalance(cash=1_000_000, positions_value=100_000)
    broker.get_open_orders.return_value = []
    broker.get_daily_order_results.return_value = []
    broker.get_same_day_buys.return_value = {
        "AAAA": SameDayBuy(ticker="AAAA", quantity=10, avg_price=9_900),
    }
    risk = RiskManager(broker=broker, db=db, config=RiskConfig())
    manager = OrderManager(broker=broker, risk=risk, db=db)
    db.add(
        OrderLog(
            order_id="o-same-day-buy",
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

    row = db.query(OrderLog).filter(OrderLog.order_id == "o-same-day-buy").one()
    assert result["updated_orders"] == 1
    assert row.status == OrderStatus.FILLED.value
    assert row.fill_qty == 10
    assert row.fill_price == 9_900


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
