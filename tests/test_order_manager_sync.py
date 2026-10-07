"""Broker reconciliation uses exact account/environment/date identities."""
import datetime as dt
from dataclasses import replace
import pytest
from maps.common.exceptions import BrokerAdapterError, ExecutionBlockedError
from maps.common.models import OrderLog
from maps.common.settings import MapsSettings
from maps.execution.broker_adapter import OrderResult, OrderSide, OrderStatus
from maps.execution.reconciliation import audit_id
from tests.test_execution_safety import setup, order, context


def test_sync_broker_state_updates_exact_fill_status(db, setup):
    broker, manager = setup
    result = manager.submit(order(), context=context())
    assert manager.sync_broker_state()["complete"]
    row = db.query(OrderLog).filter_by(order_id=result.order_id).one()
    assert row.fill_qty == 10 and row.fill_price == 1000 and row.status == "filled"


def test_sync_tolerates_history_lookup_error(db, setup):
    broker, manager = setup
    def unavailable(*args):
        raise BrokerAdapterError("disconnected")
    broker.get_order_history = unavailable
    result = manager.sync_broker_state()
    assert not result["complete"] and result["cash"] is None
    assert "broker_query_incomplete" in result["block_reasons"]


def test_legacy_unknown_is_preserved_and_blocks_new_buy(db, setup):
    broker, manager = setup
    db.add(OrderLog(order_id="legacy", strategy_id="test", ticker="AAA", side="buy",
        qty=10, fill_qty=0, status="unknown"))
    db.commit()
    assert "legacy_order_requires_resolution" in manager.sync_broker_state()["block_reasons"]
    with pytest.raises(ExecutionBlockedError):
        manager.submit(order(), context=context())
    db.expire_all()
    row = db.query(OrderLog).one()
    assert row.fill_qty == 0 and row.account_key is None


@pytest.mark.parametrize("change", ["day", "account", "environment"])
def test_reused_kis_id_never_collides_across_scopes(change):
    settings = MapsSettings(maps_broker_mode="kis", kis_account_no="12345678-01", kis_real_trading=False)
    result = OrderResult("0001", "", "AAA", OrderSide.BUY, OrderStatus.PENDING, 0, 0,
        submitted_at=dt.datetime(2026, 8, 3, 9))
    other, config = result, settings
    if change == "day":
        other = replace(result, submitted_at=result.submitted_at + dt.timedelta(days=1))
    elif change == "account":
        config = settings.model_copy(update={"kis_account_no": "87654321-01"})
    else:
        config = settings.model_copy(update={"kis_real_trading": True})
    assert audit_id(result, settings) != audit_id(other, config)


def test_kst_0855_is_scoped_to_local_order_day():
    settings = MapsSettings(maps_broker_mode="kis", kis_account_no="12345678-01")
    result = OrderResult("0001", "", "AAA", OrderSide.BUY, OrderStatus.PENDING, 0, 0,
        submitted_at=dt.datetime(2026, 8, 2, 23, 55, tzinfo=dt.timezone.utc))
    assert ":20260803:" in audit_id(result, settings)
