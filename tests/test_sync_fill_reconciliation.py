"""Missing holdings and zero-quantity responses never invent executions."""
import datetime as dt
import pytest
from maps.common.exceptions import ExecutionBlockedError
from maps.common.models import OrderLog
from maps.execution.broker_adapter import OrderResult, OrderSide, OrderStatus
from maps.execution.reconciliation import apply_result
from tests.test_execution_safety import setup


def test_prior_day_pending_sell_without_position_remains_unresolved(db, setup):
    _, manager = setup
    row = OrderLog(order_id="prior", strategy_id="test", ticker="AAA", side="sell", qty=10,
        fill_qty=0, status="pending", created_at=dt.datetime.now() - dt.timedelta(days=1))
    db.add(row); db.commit()
    assert not manager.sync_broker_state()["complete"]
    db.refresh(row)
    assert row.status == "pending" and row.fill_qty == 0 and row.fill_price is None


def test_filled_result_with_zero_quantity_is_rejected(db):
    row = OrderLog(order_id="x", strategy_id="s", ticker="AAA", side="sell", qty=10,
        fill_qty=0, status="pending", order_price=1000)
    result = OrderResult("x", "s", "AAA", OrderSide.SELL, OrderStatus.FILLED, 0, 0)
    with pytest.raises(ExecutionBlockedError):
        apply_result(db, row, result)
    assert row.fill_qty == 0 and row.fill_price is None


def test_cancelled_partial_fill_cannot_regress_or_reopen(db):
    row = OrderLog(order_id="x", strategy_id="s", ticker="AAA", side="buy", qty=10,
        fill_qty=4, fill_price=1000, status="cancelled")
    for status, quantity in [(OrderStatus.PENDING, 0), (OrderStatus.PARTIALLY_FILLED, 4)]:
        result = OrderResult("x", "s", "AAA", OrderSide.BUY, status, quantity, 1000)
        apply_result(db, row, result)
        assert row.status == "cancelled" and row.fill_qty == 4
