"""Hot policy changes and evidence-only watchlist completion, without network."""
from __future__ import annotations

import datetime as dt
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from maps.api.auth import Identity
from maps.api.ops_config import ExecutionPolicyUpdate, router, update_execution_policy
from maps.common.db import get_db
from maps.common.exceptions import ExecutionBlockedError
from maps.common.models import AnalysisPick, ExecutionPolicy, ExecutionSafetyEvent, OrderLog
from maps.common.settings import get_settings
from maps.execution.pick_reconciliation import reconcile_completed_picks
from maps.execution.runtime_policy import dispatch_source, effective_policy, policy_database
from maps.execution.safety import account_key, require_execution_enabled


def request(role="admin", origin="http://testserver"):
    """Authenticated request with an explicit same-origin browser header."""
    req = Request({"type": "http", "method": "PUT", "scheme": "http", "path": "/",
                   "server": ("testserver", 80), "headers": [(b"origin", origin.encode())]})
    req.state.user = Identity(1, "operator", role)
    return req


def update(db, enabled, version=0, **kwargs):
    """Call the real policy mutation with its account lock and audit transaction."""
    return update_execution_policy(ExecutionPolicyUpdate(enabled=enabled, scope="fujimoto_only",
        expected_version=version), request(**kwargs), db)


def test_hot_policy_overrides_cached_environment_and_persists(db):
    settings = get_settings()
    original = settings.maps_live_trading_enabled
    settings.maps_live_trading_enabled = False
    try:
        result = update(db, True)
        assert result["enabled"] and result["version"] == 1
        assert not settings.maps_live_trading_enabled  # no cache mutation
        require_execution_enabled(settings, source="fujimoto", check_scope=True)
        for source in ("catalog", "analysis_pick", "limit_up", "mock", None):
            with pytest.raises(ExecutionBlockedError, match="execution_source_not_allowed"):
                require_execution_enabled(settings, source=source, check_scope=True)
        with Session(db.get_bind()) as fresh:
            assert effective_policy(settings.model_copy(), fresh)["enabled"]
        update(db, False, 1)
        with pytest.raises(ExecutionBlockedError, match="trading_disabled"):
            require_execution_enabled(settings, source="fujimoto", check_scope=True)
        assert db.query(ExecutionSafetyEvent).filter_by(reason_code="execution_policy_changed").count() == 2
    finally:
        settings.maps_live_trading_enabled = original


def test_conflicting_version_cannot_overwrite_saved_permission(db):
    update(db, False)
    with pytest.raises(HTTPException) as exc:
        update(db, True)
    assert exc.value.status_code == 409
    assert not effective_policy(get_settings(), db)["enabled"]


@pytest.mark.parametrize("kwargs", [{"role": "user"}, {"origin": "https://attacker.example"}, {"origin": "null"}])
def test_mutation_requires_admin_and_same_origin(db, kwargs):
    with pytest.raises(HTTPException) as exc:
        update(db, True, **kwargs)
    assert exc.value.status_code == 403
    assert db.query(ExecutionPolicy).count() == 0


def test_api_rejects_broad_scope_and_extra_fields(db):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as client:
        for extra in ({"scope": "all"}, {"enabled": "true"}, {"unexpected": True}):
            payload = {"enabled": True, "scope": "fujimoto_only", "expected_version": 0, **extra}
            assert client.put("/api/v1/ops/config/execution", json=payload,
                              headers={"Origin": "http://testserver"}).status_code == 422


def test_real_account_cannot_be_enabled_and_dry_run_still_blocks(db):
    settings = get_settings()
    update(db, True)
    dry = settings.model_copy(update={"maps_dry_run": True})
    with pytest.raises(ExecutionBlockedError, match="dry_run"):
        require_execution_enabled(dry, source="fujimoto", check_scope=True)
    old = settings.kis_real_trading
    settings.kis_real_trading = True
    try:
        with pytest.raises(HTTPException, match="paper_environment_required"):
            update(db, True, 1)
    finally:
        settings.kis_real_trading = old


def test_storage_failure_blocks_dispatch():
    engine = create_engine("sqlite:///:memory:")
    try:
        with policy_database(engine), pytest.raises(ExecutionBlockedError, match="execution_policy_unavailable"):
            require_execution_enabled(get_settings(), source="fujimoto", check_scope=True)
    finally:
        engine.dispose()


def test_direct_broker_cannot_bypass_source_gate(db):
    from maps.execution.mock_broker import MockBroker
    from maps.execution.broker_adapter import Order, OrderSide, OrderType
    update(db, True)
    broker = MockBroker(10000, {"AAA": 100})
    order = Order("untrusted", "AAA", OrderSide.BUY, OrderType.LIMIT, 1, limit_price=100)
    with pytest.raises(ExecutionBlockedError, match="execution_source_not_allowed"):
        broker.place_order(order)
    with pytest.raises(ExecutionBlockedError, match="execution_source_not_allowed"):
        broker.cancel_order("unknown")
    assert broker.get_position("AAA") is None


def sold_pick(db, *, exit_status="filled", exit_fill=10, entry_key="historical", exit_key="historical"):
    """Create explicit legacy ownership, with no reliance on current holdings."""
    pick = AnalysisPick(ticker="AAA", name="Example", ref_date=dt.date.today(),
        state="BOUGHT", strategy_trade_enabled=True, trade_mode="single",
        entry_order_id="entry", exit_order_id="exit", exit_reason="stop_loss")
    db.add(pick)
    for oid, side, status, qty, key in [("entry", "buy", "filled", 10, entry_key),
                                       ("exit", "sell", exit_status, exit_fill, exit_key)]:
        db.add(OrderLog(order_id=oid, account_key=key, environment="kis_paper", ticker="AAA",
            strategy_id="strategy_trade", side=side, qty=10, fill_qty=qty, status=status, fill_price=100))
    db.commit()
    return pick


def test_delayed_full_exit_closes_without_execution_or_position_lookup(db):
    pick = sold_pick(db, exit_status="pending", exit_fill=0)
    update(db, False)
    assert not reconcile_completed_picks(db)[0]["eligible"]
    exit_row = db.query(OrderLog).filter_by(order_id="exit").one()
    exit_row.status, exit_row.fill_qty = "filled", 10
    db.flush()
    preview = reconcile_completed_picks(db, pick_ids=[pick.id], apply=False)
    assert preview[0]["eligible"] and pick.state == "BOUGHT"
    reconcile_completed_picks(db, pick_ids=[pick.id])
    db.commit()
    assert pick.state == "CLOSED" and not pick.strategy_trade_enabled
    assert pick.exit_reason == "stop_loss" and pick.exit_order_id == "exit"
    assert reconcile_completed_picks(db) == []
    assert db.query(ExecutionSafetyEvent).filter_by(reason_code="analysis_pick_completed").count() == 1


@pytest.mark.parametrize("kwargs", [{"exit_status": "partially_filled", "exit_fill": 5},
    {"exit_fill": 5}, {"exit_key": "different"}, {"entry_key": None}])
def test_unproven_exit_does_not_close_pick(db, kwargs):
    pick = sold_pick(db, **kwargs)
    assert not reconcile_completed_picks(db)[0]["eligible"]
    assert pick.state == "BOUGHT"


def test_current_account_sync_does_not_adopt_historical_orders(db):
    pick = sold_pick(db)
    assert reconcile_completed_picks(db, key=account_key()) == []
    assert pick.state == "BOUGHT"


def test_manager_blocks_other_source_buys_sells_and_cancels(db):
    from unittest.mock import Mock
    from maps.execution.order_manager import OrderManager
    from maps.execution.broker_adapter import Order, OrderSide, OrderType
    from maps.execution.safety import ExecutionContext
    update(db, True)
    broker = Mock()
    manager = OrderManager(broker, Mock(), db)
    for side in (OrderSide.BUY, OrderSide.SELL):
        order = Order("other", "AAA", side, OrderType.LIMIT, 1, limit_price=100)
        with pytest.raises(ExecutionBlockedError, match="execution_source_not_allowed"):
            manager.submit(order, context=ExecutionContext("other", source="catalog"))
    db.add(OrderLog(order_id="other-order", account_key=account_key(), environment="mock",
        ticker="AAA", side="buy", qty=1, fill_qty=0, status="pending"))
    db.commit()
    assert not manager.can_cancel("other-order")
    with pytest.raises(ExecutionBlockedError, match="execution_source_not_allowed"):
        manager.cancel("other-order")
    broker.place_order.assert_not_called()
    broker.cancel_order.assert_not_called()


def test_disable_waits_for_inflight_send_then_blocks_next_send(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from maps.common.db import Base
    from maps.execution.safety import broker_execution_boundary
    engine = create_engine(f"sqlite:///{(tmp_path / 'policy.db').as_posix()}")
    Base.metadata.create_all(engine)
    entered, release, updating = Event(), Event(), Event()

    class Broker:
        @broker_execution_boundary
        def send(self):
            entered.set()
            assert release.wait(5)

    def send():
        with policy_database(engine), dispatch_source("fujimoto"):
            Broker().send()

    def disable():
        with Session(engine) as db:
            updating.set()
            return update(db, False, 1)

    try:
        with Session(engine) as db:
            update(db, True)
        with ThreadPoolExecutor(2) as pool:
            sending = pool.submit(send)
            assert entered.wait(5)
            disabling = pool.submit(disable)
            assert updating.wait(5)
            assert not disabling.done()
            release.set()
            sending.result(timeout=5)
            assert not disabling.result(timeout=5)["enabled"]
        with policy_database(engine), dispatch_source("fujimoto"), pytest.raises(ExecutionBlockedError, match="trading_disabled"):
            Broker().send()
    finally:
        release.set()
        engine.dispose()


def test_split_completion_requires_every_filled_entry_and_no_pending_order(db):
    from maps.common.models import AnalysisPickLeg
    pick = sold_pick(db)
    pick.trade_mode = "split"
    db.add(AnalysisPickLeg(pick_id=pick.id, sequence=1, entry_price=100, weight_pct=100,
        planned_qty=12, filled_qty=12, status="FILLED"))
    db.commit()
    assert not reconcile_completed_picks(db)[0]["eligible"]
    pick.legs[0].filled_qty = 10
    pending = OrderLog(order_id="next-leg", account_key="historical", environment="kis_paper",
        strategy_id=f"strategy_trade:{pick.id}:leg:2:try:1", ticker="AAA", side="buy", qty=2,
        fill_qty=0, status="pending")
    db.add(pending)
    db.commit()
    assert not reconcile_completed_picks(db)[0]["eligible"]
    pending.status = "cancelled"
    db.flush()
    assert reconcile_completed_picks(db)[0]["eligible"]
    assert pick.state == "CLOSED"
