"""Regression cases for the account-wide execution boundary (no network)."""
import datetime as dt
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from maps.common.exceptions import BrokerOrderUnknownError, ExecutionBlockedError, ExposureCapError
from maps.common.models import AccountObservation, AccountAdjustment, AnalysisPick, ExecutionAccountState, OrderIntent, OrderLog, SecurityMetadata
from maps.common.settings import MapsSettings, get_settings, reload_settings
from maps.execution.broker_adapter import Order, OrderSide, OrderType, OrderResult, OrderStatus, PendingOrder, Position, AccountBalance
from maps.execution.mock_broker import MockBroker
from maps.execution.order_manager import OrderManager
from maps.execution.reconciliation import apply_result
from maps.execution.safety import ExecutionContext, account_key, utcnow
from maps.execution.safety_admin import classify_adjustment, resolve_intent
from maps.risk.manager import RiskConfig, RiskManager


@pytest.fixture
def setup(db, monkeypatch, tmp_path):
    from maps.execution.safety import release_process_locks
    release_process_locks()
    monkeypatch.setenv("MAPS_LIVE_TRADING_ENABLED", "true")
    monkeypatch.setenv("MAPS_DRY_RUN", "false")
    monkeypatch.setenv("MAPS_BROKER_MODE", "mock")
    monkeypatch.setenv("MAPS_EXECUTION_LOCK_DIR", str(tmp_path))
    reload_settings()
    broker = MockBroker(1_000_000, {"AAA": 1000, "BBB": 1000})
    risk = RiskManager(broker, db, config=RiskConfig(position_size_limit=.5))
    manager = OrderManager(broker, risk, db, notifier=Mock(send=Mock(return_value=False)))
    manager.sync_broker_state()
    # A complete prior-day observation is required; no production warmup bypass.
    observation = db.query(AccountObservation).one()
    observation.ref_date -= dt.timedelta(days=1)
    observation.observed_at -= dt.timedelta(days=1)
    state = db.get(ExecutionAccountState, account_key())
    state.ref_date = observation.ref_date
    db.commit()
    try:
        yield broker, manager
    finally:
        release_process_locks()


def order(side=OrderSide.BUY, quantity=10, ticker="AAA", strategy="test"):
    return Order(strategy, ticker, side, OrderType.LIMIT, quantity, limit_price=1000)


def context(key="entry"):
    return ExecutionContext(key, source="mock", valid_until=utcnow() + dt.timedelta(hours=1))


@pytest.mark.parametrize("source", ["catalog", "mock"])
@pytest.mark.parametrize("field", ["sector", "theme"])
@pytest.mark.parametrize("classified", [False, True])
def test_watchlist_strategy_name_does_not_exempt_other_sources(
    db, setup, source: str, field: str, classified: bool,
) -> None:
    """Only a validated pick source may bypass classification, never its name."""
    broker, manager = setup
    setattr(manager._risk._cfg, f"{field}_exposure_limit_enabled", True)
    setattr(manager._risk._cfg, f"{field}_exposure_limit", 0.005)
    if classified:
        db.add(SecurityMetadata(
            ticker="AAA", name="Example", market="KOSPI", security_type="STOCK",
            sector="electronics", theme="semiconductors",
        ))
        db.commit()
    reason = f"{field}_exposure_exceeded" if classified else f"{field}_classification_missing"
    with pytest.raises(ExposureCapError, match=reason):
        manager.submit(order(strategy="strategy_trade"), context=ExecutionContext("entry", source=source))
    assert broker.filled_orders == []
    assert db.query(OrderIntent).count() == 0


@pytest.mark.parametrize("invalid", ["missing", "ticker", "state", "stale"])
def test_classification_exemption_requires_a_valid_watchlist_pick(db, setup, invalid: str) -> None:
    """An unvalidated analysis_pick context must not reach the broker."""
    broker, manager = setup
    manager._settings = manager._settings.model_copy(update={"maps_strategy_trade_enabled": True})
    manager._risk._cfg.sector_exposure_limit_enabled = True
    manager._risk._cfg.theme_exposure_limit_enabled = True
    pick = AnalysisPick(
        ticker="BBB" if invalid == "ticker" else "AAA", name="Example", source="manual",
        ref_date=dt.date.today() - dt.timedelta(days=60 if invalid == "stale" else 0),
        state="WATCH" if invalid == "state" else "ARMED", strategy_trade_enabled=True,
    )
    db.add(pick)
    db.commit()
    source_id = pick.id + 1 if invalid == "missing" else pick.id
    with pytest.raises(ExecutionBlockedError, match="analysis_pick_not_armed"):
        manager.submit(order(), context=ExecutionContext("entry", source="analysis_pick", source_id=source_id))
    assert broker.filled_orders == []
    assert db.query(OrderIntent).count() == 0


@pytest.mark.parametrize("field", ["sector", "theme"])
def test_catalog_classification_cap_still_counts_watchlist_holdings(db, setup, field: str) -> None:
    """Exempting a watchlist buy must not erase its exposure for other sources."""
    broker, manager = setup
    manager._settings = manager._settings.model_copy(update={"maps_strategy_trade_enabled": True})
    setattr(manager._risk._cfg, f"{field}_exposure_limit_enabled", True)
    setattr(manager._risk._cfg, f"{field}_exposure_limit", 0.005)
    pick = AnalysisPick(
        ticker="AAA", name="Example", source="manual", ref_date=dt.date.today(),
        state="ARMED", strategy_trade_enabled=True,
    )
    db.add(pick)
    db.add_all([SecurityMetadata(
        ticker=ticker, name=ticker, market="KOSPI", security_type="STOCK",
        sector="electronics", theme="semiconductors",
    ) for ticker in ("AAA", "BBB")])
    db.commit()

    result = manager.submit(order(), context=ExecutionContext("pick-entry", source="analysis_pick", source_id=pick.id))
    assert result.status == OrderStatus.FILLED
    assert broker.get_position("AAA").quantity == 10
    # BBB's 1,000 alone fits the 5,000 cap, but the watchlist already holds 10,000.
    with pytest.raises(ExposureCapError, match=f"{field}_exposure_exceeded"):
        manager.submit(order(ticker="BBB", quantity=1), context=ExecutionContext("catalog-entry", source="catalog"))
    assert broker.get_position("BBB") is None
    intent = db.query(OrderIntent).one()
    assert intent.request["source"] == "analysis_pick"
    assert intent.request["source_id"] == pick.id


@pytest.mark.parametrize("setting", ["maps_dry_run", "maps_live_trading_enabled"])
@pytest.mark.parametrize("operation", ["buy", "sell", "cancel", "cleanup"])
def test_disabled_modes_block_every_mutation(db, setup, setting, operation):
    broker, manager = setup
    manager._settings = manager._settings.model_copy(update={setting: setting == "maps_dry_run"})
    spy = Mock(wraps=broker.place_order)
    broker.place_order = spy
    with pytest.raises(ExecutionBlockedError):
        if operation == "cancel":
            manager.cancel("absent")
        elif operation == "cleanup":
            manager.eod_cleanup()
        else:
            manager.submit(order(OrderSide.BUY if operation == "buy" else OrderSide.SELL), context=context())
    spy.assert_not_called()
    assert db.query(OrderIntent).count() == 0


def test_warmup_blocks_buy_but_records_snapshot(db, setup):
    broker, manager = setup
    db.query(AccountObservation).delete()
    db.query(ExecutionAccountState).delete()
    db.commit()
    with pytest.raises(ExecutionBlockedError, match="account_warmup"):
        manager.submit(order(), context=context())
    assert db.query(AccountObservation).count() == 1
    assert broker.filled_orders == []


def test_failed_old_observation_does_not_skip_first_valid_day_warmup(db, setup):
    _, manager = setup
    old = db.query(AccountObservation).one()
    old.complete = False
    db.commit()
    assert "account_warmup" in manager.sync_broker_state()["block_reasons"]
    assert "account_warmup" in manager.sync_broker_state()["block_reasons"]


def test_intent_is_durable_before_send_and_duplicate_is_not_resent(db, setup):
    broker, manager = setup
    original = broker.place_order
    def send(request):
        intent = db.query(OrderIntent).one()
        assert intent.status == "SENDING"
        assert intent.reserved_amount == 10000
        return original(request)
    broker.place_order = Mock(side_effect=send)
    first = manager.submit(order(), context=context())
    second = manager.submit(order(), context=context())
    assert first.order_id == second.order_id
    assert broker.place_order.call_count == 1
    db.expire_all()
    assert db.query(OrderIntent).one().status == "FILLED"


@pytest.mark.parametrize("side", [OrderSide.BUY, OrderSide.SELL])
def test_timeout_is_unknown_for_both_sides_and_never_resent(db, setup, side):
    broker, manager = setup
    if side == OrderSide.SELL:
        manager.submit(order(), context=context("buy"))
    broker.place_order = Mock(side_effect=TimeoutError("ambiguous"))
    for _ in range(2):
        with pytest.raises(BrokerOrderUnknownError):
            manager.submit(order(side), context=context("ambiguous"))
    intent = db.query(OrderIntent).filter_by(event_key="ambiguous").one()
    assert intent.status == "UNKNOWN"
    assert broker.place_order.call_count == 1
    assert not manager.sync_broker_state()["complete"]


def test_idempotency_payload_conflict(db, setup):
    _, manager = setup
    manager.submit(order(), context=context())
    with pytest.raises(ExecutionBlockedError, match="payload_conflict"):
        manager.submit(order(quantity=11), context=context())


@pytest.mark.parametrize("check_classification_limits", [True, False])
def test_cumulative_exposure_includes_holding_pending_and_new(
    db, setup, check_classification_limits: bool,
) -> None:
    """Classification exemption still caps holdings plus pending and new buys."""
    broker, manager = setup
    risk = manager._risk
    with pytest.raises(ExposureCapError) as error:
        risk.check_before_order(order(quantity=100), AccountBalance(600000, 400000),
            positions={"AAA": Position("AAA", 400, 1000, current_price=1000)},
            pending_orders=[PendingOrder("p", "AAA", OrderSide.BUY, 50, 50, 1000)],
            check_classification_limits=check_classification_limits)
    assert error.value.exposure == pytest.approx(0.55)


def test_missing_market_price_is_blocked(db, setup):
    _, manager = setup
    with pytest.raises(ExposureCapError):
        manager._risk.check_before_order(replace(order(), limit_price=None, current_price=None),
            AccountBalance(1000000, 0), positions={}, pending_orders=[])


def test_monotone_partial_fills_and_no_invented_fill(db):
    row = OrderLog(order_id="p", strategy_id="test", ticker="AAA", side="sell", qty=10,
        status="partially_filled", fill_qty=4, fill_price=1000)
    db.add(row); db.commit()
    late = OrderResult("p", "test", "AAA", OrderSide.SELL, OrderStatus.PENDING, 2, 1000)
    assert not apply_result(db, row, late)
    assert row.fill_qty == 4
    with pytest.raises(ExecutionBlockedError, match="filled_quantity_missing"):
        apply_result(db, row, replace(late, status=OrderStatus.FILLED, filled_quantity=0))
    assert row.fill_qty == 4


def test_unexplained_cash_blocks_until_versioned_classification(db, setup):
    broker, manager = setup
    broker._cash += 50000
    summary = manager.sync_broker_state()
    assert "account_difference_unclassified" in summary["block_reasons"]
    adjustment = db.query(AccountAdjustment).one()
    request = SimpleNamespace(version=adjustment.version, kind="deposit", amount=Decimal(50000),
        reason="bank statement checked", evidence={"reference": "bank-1"}, quantity_changes={})
    classify_adjustment(db, get_settings(), adjustment.id, request, "admin")
    with pytest.raises(ExecutionBlockedError, match="version_conflict"):
        classify_adjustment(db, get_settings(), adjustment.id, request, "admin")
    assert manager.sync_broker_state()["complete"]
    db.expire_all()
    assert db.get(ExecutionAccountState, account_key()).daily_return == 0


def test_loss_latches_account_kill_but_verified_exit_is_allowed(db, setup):
    broker, manager = setup
    manager.submit(order(quantity=100), context=context())
    manager.sync_broker_state()
    broker.set_price("AAA", 800)
    summary = manager.sync_broker_state()
    assert "account_kill_switch" in summary["block_reasons"]
    with pytest.raises(ExecutionBlockedError, match="account_kill_switch"):
        manager.submit(order(ticker="BBB"), context=context("second"))
    result = manager.submit_exit(replace(order(OrderSide.SELL, 100), limit_price=800), context=context("exit"))
    assert result.status == OrderStatus.FILLED


def test_restart_sending_does_not_adopt_similar_manual_order(db, setup):
    broker, manager = setup
    broker.place_order = Mock(side_effect=TimeoutError())
    with pytest.raises(BrokerOrderUnknownError):
        manager.submit(order(), context=context())
    intent = db.query(OrderIntent).one()
    intent.status = "SENDING"; db.commit()
    broker._filled.append(OrderResult("manual", "", "AAA", OrderSide.BUY, OrderStatus.FILLED,
        10, 1000, quantity=10))
    manager.sync_broker_state()
    db.expire_all()
    assert intent.status == "UNKNOWN"
    assert intent.broker_order_id is None
    assert db.query(OrderLog).filter_by(order_id="manual").one().strategy_id == "external_mts"


def test_resolution_needs_evidence_and_never_resends(db, setup):
    broker, manager = setup
    broker.place_order = Mock(side_effect=TimeoutError())
    with pytest.raises(BrokerOrderUnknownError):
        manager.submit(order(), context=context())
    intent = db.query(OrderIntent).one()
    request = SimpleNamespace(version=intent.version, reason="broker support checked",
        evidence={"broker_nonacceptance_reference": "case-123"}, outcome="not_accepted", broker_order_id=None)
    resolve_intent(db, broker, get_settings(), intent.id, request, "admin")
    assert intent.status == "REJECTED"
    with pytest.raises(BrokerOrderUnknownError):
        manager.submit(order(), context=context())
    assert broker.place_order.call_count == 1


def test_stale_snapshot_blocks_even_exits(db, setup):
    broker, manager = setup
    manager.submit(order(), context=context())
    snapshot = broker.get_execution_snapshot()
    broker.get_execution_snapshot = lambda: replace(snapshot, as_of=snapshot.as_of - dt.timedelta(minutes=2))
    with pytest.raises(ExecutionBlockedError, match="snapshot_unavailable"):
        manager.submit_exit(order(OrderSide.SELL), context=context("exit"))


def test_cancel_acceptance_does_not_release_reservation(db, setup):
    broker, manager = setup
    broker.place_order = lambda request: OrderResult("pending", request.strategy_id, request.ticker,
        request.side, OrderStatus.PENDING, 0, 0, quantity=request.quantity)
    result = manager.submit(order(), context=context())
    broker.cancel_order = lambda oid: True
    assert not manager.cancel(result.order_id)
    db.expire_all()
    intent = db.query(OrderIntent).one()
    assert intent.reserved_amount == 10000
    assert intent.cancel_requested


def test_no_midnight_expiration_without_broker_evidence(db, setup):
    broker, manager = setup
    broker.place_order = Mock(side_effect=TimeoutError())
    with pytest.raises(BrokerOrderUnknownError):
        manager.submit(order(), context=context())
    assert manager.expire_pending_orders(before=utcnow() + dt.timedelta(days=20)) == 0
    assert db.query(OrderIntent).one().status == "UNKNOWN"


def test_never_sent_expired_intent_releases_its_reservation(db, setup):
    broker, manager = setup
    broker.place_order = Mock(side_effect=ExecutionBlockedError("trading_disabled"))
    with pytest.raises(ExecutionBlockedError):
        manager.submit(order(), context=context())
    intent = db.query(OrderIntent).one()
    assert intent.status == "PREPARED"
    intent.valid_until = utcnow() - dt.timedelta(seconds=1)
    db.commit()
    manager.sync_broker_state()
    db.refresh(intent)
    assert intent.status == "REJECTED"
    assert intent.reserved_amount == 0


def test_notification_failure_is_persisted_and_retried(db, setup):
    from maps.common.models import ExecutionSafetyEvent
    from maps.ops.safety_notifications import deliver_safety_events
    _, manager = setup
    manager.sync_broker_state()
    row = db.query(ExecutionSafetyEvent).first()
    assert row.delivered_at is None and row.attempts > 0
    row.next_attempt_at = utcnow() - dt.timedelta(seconds=1)
    db.commit()
    deliver_safety_events(db, Mock(send=Mock(return_value=True)), account_key())
    db.refresh(row)
    assert row.delivered_at is not None


def test_single_writer_is_enforced_across_processes(setup):
    import os
    import subprocess
    import sys
    from maps.execution.safety import account_execution_lock
    key = account_key()
    script = """
from maps.common.settings import MapsSettings
MapsSettings.model_config['env_file'] = None
from maps.execution.safety import account_execution_lock
from maps.common.exceptions import ExecutionBlockedError
try:
    with account_execution_lock(%r):
        raise SystemExit(2)
except ExecutionBlockedError:
    raise SystemExit(0)
""" % key
    with account_execution_lock(key):
        result = subprocess.run([sys.executable, "-c", script], cwd=os.getcwd(), timeout=30,
            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("key,value", [("wfa_passed", False), ("mc_passed", False),
    ("oos_sharpe", .29), ("plateau_grade", "F"), ("evidence_valid", False)])
def test_high_weighted_score_cannot_override_hard_validation(db, key, value):
    from maps.promotion.gate import PromotionGate, PromotionStage
    metrics = dict(robustness=1, risk=1, recovery=1, **{"return": 1}, mc_mdd_p95=.01,
        mock_months=4, evidence_valid=True, wfa_passed=True, mc_passed=True,
        plateau_grade="A", oos_sharpe=1, validation_run_id="test-run")
    metrics[key] = value
    result = PromotionGate(db).evaluate("pullback_v3", metrics, PromotionStage.MOCK_CANDIDATE)
    assert result.score == 100 and not result.passed


def test_concurrent_same_event_sends_once(setup, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from maps.common.db import Base
    from tests.execution_contract import prime_account
    broker, _ = setup
    engine = create_engine(f"sqlite:///{(tmp_path / 'concurrent.db').as_posix()}",
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        manager = OrderManager(broker, RiskManager(broker, db), db)
        prime_account(manager, db)
    broker.place_order = Mock(wraps=broker.place_order)
    def submit(_):
        with Session(engine) as db:
            return OrderManager(broker, RiskManager(broker, db), db).submit(order(), context=context())
    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(submit, range(6)))
    finally:
        engine.dispose()
    assert len({r.order_id for r in results}) == 1
    assert broker.place_order.call_count == 1


def test_legacy_exact_link_stays_linked_on_later_sync(db, setup):
    from maps.execution.safety_admin import resolve_legacy_order
    broker, manager = setup
    result = broker.place_order(order())
    legacy = OrderLog(order_id="legacy-id", strategy_id="test", ticker="AAA", side="buy",
                      qty=10, fill_qty=0, status="pending")
    db.add(legacy)
    db.commit()
    state = db.get(ExecutionAccountState, account_key())
    request = SimpleNamespace(version=state.version, reason="exact broker statement reviewed",
        evidence={"statement": "verified-reference"}, outcome="link", broker_order_id=result.order_id)
    resolve_legacy_order(db, broker, get_settings(), legacy.id, request, "admin")
    manager.sync_broker_state()
    db.expire_all()
    assert legacy.order_id == "legacy-id" and legacy.fill_qty == 10
    assert db.query(OrderLog).count() == 1


def test_admin_required_and_account_release_checks_version(db, setup):
    from fastapi import HTTPException
    from maps.api.risk import _safety_actor, release_account_kill
    from maps.api.schemas import SafetyResolutionRequest
    with pytest.raises(HTTPException) as denied:
        _safety_actor(SimpleNamespace(state=SimpleNamespace(user=SimpleNamespace(is_admin=False))))
    assert denied.value.status_code == 403
    _, manager = setup
    manager.sync_broker_state()
    db.expire_all()
    state = db.get(ExecutionAccountState, account_key())
    state.killed = True
    state.block_reasons = ["account_kill_switch"]
    db.commit()
    request = SimpleNamespace(state=SimpleNamespace(user=SimpleNamespace(is_admin=True, username="admin")))
    body = SafetyResolutionRequest(version=state.version + 1, reason="account review completed",
                                   evidence={"review": "case-1"})
    with pytest.raises(HTTPException) as conflict:
        release_account_kill(body, request, db)
    assert conflict.value.status_code == 409
    body.version = state.version
    release_account_kill(body, request, db)
    assert not state.killed


def test_average_cost_cannot_replace_current_position_valuation(db, setup):
    _, manager = setup
    with pytest.raises(ExposureCapError, match="position_valuation_missing"):
        manager._risk.check_before_order(order(), AccountBalance(900000, 100000),
            positions={"AAA": Position("AAA", 100, 1000)}, pending_orders=[])
