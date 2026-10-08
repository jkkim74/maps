"""Offline source, reservation and control boundaries for Fujimoto execution."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from maps.common.exceptions import ExecutionBlockedError
from maps.common.settings import MapsSettings
from maps.execution.broker_adapter import Order, OrderSide, OrderType
from maps.execution.order_manager import OrderManager
from maps.execution.safety import ExecutionContext, account_key
from maps.fujimoto.domain import Mode, CycleState, RuleEvidence, evaluate
from maps.fujimoto.repository import FujimotoRepository


def seeded(db, settings=None):
    settings = settings or MapsSettings(_env_file=None)
    repo = FujimotoRepository(db)
    key = account_key(settings)
    for mode in Mode:
        repo.configure(key, 7, mode, 500000)
    cycle = repo.create_cycle(repo.configurations(key)[1].id, "AAA")
    signal = RuleEvidence(date(2026, 10, 7), 1000, True,
        financial_status="maintained", daily_rsi=40, weekly_rsi=50)
    evidence = repo.record_evidence("candidate", "AAA", datetime(2026, 10, 7, 7),
        datetime(2026, 10, 7, 7), {"rule": {}})
    decision = evaluate(Mode.ORIGINAL, signal, CycleState())
    reservation = repo.reserve_order(cycle.id, decision, evidence.id, 2, 1000,
        signal_date=signal.as_of)
    db.commit()
    return repo, cycle, reservation


def test_strategy_identity_cannot_bypass_fujimoto_source(db):
    settings = MapsSettings(_env_file=None, maps_broker_mode="mock")
    manager = OrderManager(None, None, db, settings=settings)
    order = Order(Mode.SAFE.strategy_id, "AAA", OrderSide.BUY, OrderType.LIMIT, 1, 1000)
    with pytest.raises(ExecutionBlockedError, match="fujimoto_source_required"):
        manager._entry_policy(db, order, ExecutionContext("forged", source="mock"))


def test_stop_is_durable_with_pending_and_preserves_exit_consent(db):
    from maps.fujimoto.service import FujimotoService
    repo, cycle, reservation = seeded(db)
    now = datetime.now(timezone.utc)
    repo.record_evidence("control", "*", now, now,
        {"execution_mode": "paper", "entries_enabled": True, "sell_consent": True,
         "owner_user_id": 7}, account_key=cycle.account_key)
    db.commit()
    service = FujimotoService(db)
    service.stop(cycle.account_key, 7)
    assert service.control(cycle.account_key)["entries_enabled"] is False
    assert service.control(cycle.account_key)["sell_consent"] is True
    assert repo.state(cycle.id).pending_order
    assert repo.reserved_cash(cycle.config_id) > 2000
    with pytest.raises(ExecutionBlockedError, match="owner"):
        service.stop(cycle.account_key, 8)


def test_default_control_never_grants_execution(db):
    from maps.fujimoto.service import FujimotoService
    assert FujimotoService(db).control("unconfigured")["execution_mode"] == "observe"


def test_unchanged_hold_decision_does_not_grow_audit_on_every_quote(db):
    from maps.common.models import FujimotoEvidence
    from maps.fujimoto.domain import Decision
    repo, cycle, order = seeded(db)
    for _ in range(5):
        repo.record_decision(cycle.id, order.evidence_id, Decision("hold", "pending_order"))
    assert db.query(FujimotoEvidence).filter_by(kind="decision").count() == 2  # initial BUY + one HOLD


def test_runtime_rejects_research_cost_or_cash_floor_mismatch(db):
    from maps.fujimoto.service import FujimotoService
    repo, cycle, _ = seeded(db)
    now = datetime.now(timezone.utc)
    record = repo.record_evidence("replay", "*", now, now,
        {"inputs": {"budget": 1000000, "fee_rate": 0}}, account_key=cycle.account_key)
    with pytest.raises(ExecutionBlockedError, match="validation_execution_parameters_mismatch"):
        FujimotoService(db).validation_gate(cycle.account_key, record.id, recompute=True)


def test_broker_exact_cost_correction_is_not_silently_discarded(db):
    from maps.execution.reconciliation import apply_result
    from maps.execution.broker_adapter import OrderResult, OrderStatus
    repo, cycle, reservation = seeded(db)
    _, log = linked_intent(db, cycle, reservation)
    for fee in (1, 3):
        apply_result(db, log, OrderResult("broker", Mode.ORIGINAL.strategy_id, "AAA", OrderSide.BUY,
            OrderStatus.FILLED, filled_quantity=2, avg_price=1000, cumulative_gross=2000,
            commission=fee, tax=0, costs_complete=True))
    assert reservation.fees == 3
    assert cycle.cost_basis == 2003


def test_observation_expiry_does_not_expire_unknown_orders(db):
    from maps.fujimoto.service import FujimotoService
    repo, cycle, reservation = seeded(db)
    reservation.status = "UNKNOWN"
    other = repo.create_cycle(cycle.config_id, "BBB")
    db.commit()
    FujimotoService(db).expire_empty_cycles(cycle.account_key, date(2026, 10, 9))
    assert repo.state(cycle.id).pending_order
    assert reservation.status == "UNKNOWN"
    assert repo.evidence_as_of("cycle_expired", other.ticker, datetime.max,
        account_key=cycle.account_key)[-1].payload["cycle_id"] == other.id


def test_combined_reservations_count_linked_intent_once(db):
    from maps.fujimoto.service import reserved_exposure
    repo, cycle, reservation = seeded(db)
    amount, by_ticker = reserved_exposure(db, cycle.account_key, [])
    assert amount == Decimal("2000.30000")
    assert by_ticker["AAA"] == amount


def test_live_account_cannot_activate_paper(db):
    from maps.fujimoto.service import FujimotoService
    settings = MapsSettings(_env_file=None, maps_broker_mode="kis", kis_real_trading=True)
    repo, cycle, reservation = seeded(db, settings)
    with pytest.raises(ExecutionBlockedError, match="paper_account_required"):
        FujimotoService(db, settings=settings).activate(cycle.account_key, 7,
            execution_mode="paper", replay_id=1, sell_consent=True)


def test_disabled_global_execution_rejects_activation_before_research(db, monkeypatch):
    from maps.fujimoto.service import FujimotoService
    settings = MapsSettings(_env_file=None, maps_live_trading_enabled=False, maps_dry_run=False,
                            maps_fujimoto_enabled=True)
    service = FujimotoService(db, settings=settings)
    monkeypatch.setattr(service, "validation_gate", lambda *a, **kw: pytest.fail("research before global guard"))
    with pytest.raises(ExecutionBlockedError, match="trading_disabled"):
        service.activate(account_key(settings), 7, execution_mode="paper", replay_id=1, sell_consent=True)


def linked_intent(db, cycle, reservation, *, status="ACKNOWLEDGED"):
    from maps.common.models import OrderIntent, OrderLog
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    intent = OrderIntent(id="intent", account_key=cycle.account_key, environment="mock",
        event_key=f"fujimoto:{reservation.id}", strategy_id=Mode(cycle.mode).strategy_id,
        ticker=cycle.ticker, side="buy", status=status, quantity=reservation.quantity,
        filled_quantity=0, request={"source": "fujimoto", "source_id": cycle.id,
            "price_bound": "1000", "fujimoto_approval_id": 1},
        valid_until=now + timedelta(hours=1), created_at=now, updated_at=now,
        reserved_amount=2000, reserved_quantity=0, broker_order_id="broker")
    row = OrderLog(order_id="broker", intent_id="intent", account_key=cycle.account_key,
        environment="mock", strategy_id=Mode(cycle.mode).strategy_id, ticker=cycle.ticker,
        side="buy", qty=reservation.quantity, fill_qty=0, status="pending", broker="mock", mode="mock")
    db.add_all([intent, row])
    db.commit()
    return intent, row


def test_reconciliation_recovers_unbound_intent_and_partial_fill_idempotently(db):
    from maps.execution.reconciliation import apply_result
    from maps.execution.broker_adapter import OrderResult, OrderStatus
    repo, cycle, reservation = seeded(db)
    intent, row = linked_intent(db, cycle, reservation)
    result = OrderResult("broker", Mode.ORIGINAL.strategy_id, "AAA", OrderSide.BUY,
        OrderStatus.PARTIALLY_FILLED, filled_quantity=1, avg_price=1000)
    apply_result(db, row, result)
    db.commit()
    db.expire_all()
    assert reservation.intent_id == intent.id
    assert repo.state(cycle.id).quantity == 1 and repo.state(cycle.id).pending_order
    apply_result(db, row, result)
    db.commit()
    assert repo.state(cycle.id).quantity == 1


def test_quantity_terminal_cost_unknown_does_not_strand_owned_exits(db):
    from maps.fujimoto.service import FujimotoService
    from maps.execution.reconciliation import apply_result
    from maps.execution.broker_adapter import OrderResult, OrderStatus
    repo, cycle, reservation = seeded(db)
    intent, row = linked_intent(db, cycle, reservation)
    apply_result(db, row, OrderResult("broker", Mode.ORIGINAL.strategy_id, "AAA", OrderSide.BUY,
        OrderStatus.FILLED, filled_quantity=2, avg_price=1000))
    db.commit()
    assert repo.state(cycle.id).quantity == 2
    assert not repo.state(cycle.id).pending_order
    assert repo.state(cycle.id).buy_stage == 1
    assert FujimotoService(db).unresolved_costs(cycle.account_key) == [reservation.id]


def test_recovery_never_resubmits_unknown_intent(db):
    from maps.fujimoto.service import FujimotoService
    repo, cycle, reservation = seeded(db)
    intent, row = linked_intent(db, cycle, reservation, status="UNKNOWN")
    service = FujimotoService(db)
    service.recover_links(cycle.account_key)
    assert reservation.intent_id == intent.id
    assert reservation.status == "UNKNOWN"
    assert repo.state(cycle.id).pending_order


def test_observe_feed_does_not_call_broker_without_budget(db):
    from maps.fujimoto.service import FujimotoService
    from maps.limit_up.feed import FeedQuote
    class ForbiddenManager:
        def __getattr__(self, name):
            raise AssertionError("observe called broker manager")
    now = datetime.now(timezone.utc)
    service = FujimotoService(db, ForbiddenManager())
    service.on_quote(FeedQuote("AAA", 1001, 100, 1000, 300, 1.,
        100, 300, now, now), now=now)
    assert service.repo.evidence_as_of("quote", "AAA", datetime.max,
        account_key=account_key())


@pytest.mark.parametrize("scenario", ["stop", "financial", "book", "technical_due", "technical_stale", "technical_stale_rounding", "same_day_add", "partial_stop", "partial_financial", "partial_book"])
def test_on_quote_today_fills_keep_prior_bar_date_and_protect_all_cycles(db, monkeypatch, scenario):
    from unittest.mock import Mock
    from maps.fujimoto.service import FujimotoService
    from maps.fujimoto.repository import FillEvent
    from maps.limit_up.feed import FeedQuote
    from maps.common.models import FujimotoEvidence
    now = datetime(2026, 10, 8, 1, tzinfo=timezone.utc)
    monkeypatch.setattr("maps.fujimoto.service.utcnow", lambda: now.replace(tzinfo=None))
    settings = MapsSettings(_env_file=None, maps_fujimoto_enabled=True)
    repo = FujimotoRepository(db)
    key = account_key(settings)
    rule = RuleEvidence(date(2026, 10, 6 if scenario.startswith("technical_stale") else 7), 1000, True,
        financial_status="maintained", daily_rsi=40, weekly_rsi=50, macd_golden=True,
        rsi_cross_70=scenario.startswith("technical"))
    source = repo.record_evidence("candidate", "AAA", now - timedelta(days=1),
        now - timedelta(days=1), {"rule": rule})
    quantity = 2 if scenario == "technical_stale_rounding" else 18
    configs = {mode: repo.configure(key, 7, mode, 5000000) for mode in Mode}
    for mode in Mode:
        config = configs[mode]
        cycle = repo.create_cycle(config.id, "AAA")
        order = repo.reserve_order(cycle.id, evaluate(mode, rule, CycleState()), source.id,
            quantity, 1000, signal_date=rule.as_of, stop_price=920 if mode == Mode.SAFE else None)
        filled = 2 if scenario.startswith("partial_") else quantity
        order.broker_order_id = "broker-" + mode.value
        repo.apply_fill(FillEvent(order.id, key, None, filled, filled * 1000, 0, 0,
                                 "PARTIAL" if scenario.startswith("partial_") else "FILLED", now.date()))
        if scenario == "book":
            repo.record_evidence("order_cost", str(order.id), now, now, {"complete": True}, account_key=key)
    repo.record_evidence("control", "*", now - timedelta(minutes=1), now - timedelta(minutes=1),
        {"execution_mode": "paper", "entries_enabled": scenario == "same_day_add", "sell_consent": True}, account_key=key)
    db.commit()
    monkeypatch.setattr("maps.fujimoto.sources.current_financial_status",
                        lambda *a: "deteriorated" if scenario in {"financial", "partial_financial"} else "maintained")
    service = FujimotoService(db, Mock(), settings=settings)
    submitted = []
    monkeypatch.setattr(service, "_submit_decision", lambda cycle, evidence, decision, *a:
        submitted.append((cycle.mode, evidence.as_of, decision.reason)))
    bid = 900 if scenario in {"stop", "partial_stop"} else 1100
    for second in range(31 if scenario == "book" else 1):
        received = now + timedelta(seconds=second)
        quote = FeedQuote("AAA", bid + 1, 100, bid, 400, float(second), 100, 400, received, received)
        service.on_quote(quote, now=received)
    if scenario.startswith("partial_"):
        assert submitted == []
        assert all(repo.state(c.id).quantity == 2 and repo.state(c.id).buy_stage == 0
                   and repo.state(c.id).pending_order for c in repo.cycles(key))
        assert service.manager.cancel.call_count == (2 if scenario == "partial_financial" else 1 if scenario == "partial_stop" else 0)
        assert all(repo.reserved_cash(c.id) > 0 for c in repo.configurations(key))
    elif scenario == "financial":
        assert submitted == [(mode.value, rule.as_of, "fundamental_deterioration") for mode in Mode]
    elif scenario == "book":
        assert submitted == [(mode.value, rule.as_of, "orderbook_take_profit") for mode in Mode]
    elif scenario == "technical_due":
        assert submitted == [(mode.value, rule.as_of, "rsi_cross_70") for mode in Mode]
    elif scenario.startswith("technical_stale"):
        assert submitted == []
        assert all(repo.state(c.id).sell_target_ninths == 0 for c in repo.cycles(key))
    elif scenario == "stop":
        assert submitted == [("safe", rule.as_of, "price_stop")]
        holds = db.query(FujimotoEvidence).filter_by(kind="decision").all()
        assert "same_day_advancement" in holds[-1].payload["decision"]["reasons"]
    else:
        assert submitted == []
        holds = db.query(FujimotoEvidence).filter_by(kind="decision").all()
        assert "same_day_advancement" in holds[-1].payload["decision"]["reasons"]


def test_exact_late_buy_fee_after_partial_sell_is_audited_and_allocated(db):
    from maps.common.models import FujimotoFill
    from maps.fujimoto.service import FujimotoService
    from maps.fujimoto.domain import Decision
    from maps.fujimoto.repository import FillEvent
    repo, cycle, buy = seeded(db)
    intent, row = linked_intent(db, cycle, buy)
    repo.bind_intent(buy.id, intent.id, "broker")
    repo.apply_fill(FillEvent(buy.id, cycle.account_key, intent.id, 2, 2000, 0, 0, "FILLED", date(2026, 10, 8)))
    sell = repo.reserve_order(cycle.id, Decision("sell", "financial_deterioration", sell_quantity=1),
        buy.evidence_id, 1, 1200)
    repo.apply_fill(FillEvent(sell.id, cycle.account_key, None, 1, 1200, 0, 0, "FILLED", date(2026, 10, 8)))
    db.commit()
    service = FujimotoService(db)
    service.settle_costs(cycle.account_key, 7, buy.id, gross=2000, fees=20, tax=0,
        evidence={"broker_order_id": "broker", "source_url": "https://broker.example/order",
                  "document_hash": "a" * 64})
    assert cycle.cost_basis == 1010
    assert cycle.realized_pnl == 190
    assert repo.cash(cycle.config_id) == 499180
    assert db.query(FujimotoFill).filter_by(order_id=buy.id).count() == 2


def test_linked_reservation_not_counted_twice_and_external_order_sees_unbound(db):
    from maps.fujimoto.service import reserved_exposure, unbound_reservations
    repo, cycle, reservation = seeded(db)
    assert sum(p.remaining_quantity for p in unbound_reservations(db, cycle.account_key)) == 2
    assert unbound_reservations(db, cycle.account_key, exclude_event=f"fujimoto:{reservation.id}") == []
    intent, row = linked_intent(db, cycle, reservation)
    repo.bind_intent(reservation.id, intent.id, "broker")
    db.commit()
    total, _ = reserved_exposure(db, cycle.account_key, [])
    assert total == Decimal("2000.30000")
    assert unbound_reservations(db, cycle.account_key) == []


def test_paper_gate_uses_mock_promotion_without_requiring_live_track_record(db, monkeypatch):
    from maps.fujimoto.service import require_paper_eligibility
    from maps.common.models import PromotionHistory
    db.add(PromotionHistory(strategy_id=Mode.SAFE.strategy_id, from_stage="research",
        to_stage="mock_candidate", passed=True, tradeability_score=80))
    db.commit()
    metrics = {"validation_run_id": "measured", "evidence_valid": False,
        "evidence_errors": ["insufficient_completed_trades_or_track_record"],
        "robustness": .9, "risk": .9, "recovery": .9, "return": .9,
        "mc_passed": True, "plateau_grade": "A", "oos_sharpe": 1., "wfa_passed": True}
    monkeypatch.setattr("maps.promotion.evidence.evidence_metrics", lambda *args: metrics)
    assert require_paper_eligibility(db, Mode.SAFE.strategy_id, MapsSettings(_env_file=None)) == "measured"


@pytest.mark.parametrize("field", ["account", "ticker", "strategy", "quantity", "price", "event"])
def test_source_rejects_forged_cycle_reservation_identity(db, field):
    from maps.fujimoto.service import validate_source
    settings = MapsSettings(_env_file=None)
    repo, cycle, reservation = seeded(db, settings)
    order = Order(Mode.ORIGINAL.strategy_id, cycle.ticker, OrderSide.BUY, OrderType.LIMIT, 2, 1000)
    context = ExecutionContext(f"fujimoto:{reservation.id}", source="fujimoto", source_id=cycle.id)
    if field == "account":
        cycle.account_key = "foreign"
    elif field == "ticker":
        order.ticker = "BBB"
    elif field == "strategy":
        order.strategy_id = Mode.SAFE.strategy_id
    elif field == "quantity":
        order.quantity = 3
    elif field == "price":
        order.limit_price = 1001
    else:
        context = ExecutionContext("forged", source="fujimoto", source_id=cycle.id)
    with pytest.raises(ExecutionBlockedError, match="fujimoto_(cycle|reservation)"):
        validate_source(db, order, context, settings)


def test_cancel_request_does_not_release_reservation_before_terminal_confirmation(db):
    from maps.execution.reconciliation import apply_result
    from maps.execution.broker_adapter import OrderResult, OrderStatus
    repo, cycle, reservation = seeded(db)
    intent, row = linked_intent(db, cycle, reservation)
    intent.cancel_requested = True
    apply_result(db, row, OrderResult("broker", Mode.ORIGINAL.strategy_id, "AAA", OrderSide.BUY,
        OrderStatus.PARTIALLY_FILLED, filled_quantity=1, avg_price=1000))
    assert reservation.status == "CANCEL_REQUESTED"
    assert repo.state(cycle.id).pending_order
    assert repo.reserved_cash(cycle.config_id) > 1000
    apply_result(db, row, OrderResult("broker", Mode.ORIGINAL.strategy_id, "AAA", OrderSide.BUY,
        OrderStatus.FILLED, filled_quantity=2, avg_price=1000))
    assert repo.state(cycle.id).quantity == 2
    assert not repo.state(cycle.id).pending_order


def test_unknown_order_same_event_is_not_submitted_twice(db, monkeypatch):
    from unittest.mock import Mock
    from maps.common.exceptions import BrokerOrderUnknownError
    repo, cycle, reservation = seeded(db)
    intent, row = linked_intent(db, cycle, reservation, status="UNKNOWN")
    intent.broker_order_id = None
    intent.request = {**intent.request, "order_type": "limit", "limit_price": 1000}
    db.commit()
    broker = Mock()
    manager = OrderManager(broker, Mock(), db)
    order = Order(Mode.ORIGINAL.strategy_id, "AAA", OrderSide.BUY, OrderType.LIMIT, 2, 1000)
    for _ in range(2):
        with pytest.raises(BrokerOrderUnknownError):
            manager.submit(order, context=ExecutionContext(f"fujimoto:{reservation.id}",
                source="fujimoto", source_id=cycle.id))
    broker.place_order.assert_not_called()


@pytest.mark.parametrize("costs_complete", [True, False])
@pytest.mark.parametrize("mode", list(Mode))
def test_service_order_manager_fill_and_stopped_owned_exit_flow(db, monkeypatch, tmp_path, costs_complete, mode):
    """Only measured promotion is a fixture; real mock execution/reconciliation is exercised."""
    from unittest.mock import Mock
    from maps.common.models import AccountObservation, ExecutionAccountState
    from maps.execution.mock_broker import MockBroker
    from maps.execution.safety import release_process_locks, account_execution_lock, utcnow
    from maps.fujimoto.service import FujimotoService
    from maps.fujimoto.domain import Decision
    from maps.risk.manager import RiskConfig, RiskManager
    from maps.common.settings import reload_settings
    for name, value in {"MAPS_LIVE_TRADING_ENABLED": "true", "MAPS_DRY_RUN": "false",
        "MAPS_BROKER_MODE": "mock", "MAPS_FUJIMOTO_ENABLED": "true",
        "MAPS_EXECUTION_LOCK_DIR": str(tmp_path)}.items():
        monkeypatch.setenv(name, value)
    settings = reload_settings()
    release_process_locks()
    broker = MockBroker(1000000, {"AAA": 1000})
    if not costs_complete:
        from dataclasses import replace
        place = broker.place_order
        monkeypatch.setattr(broker, "place_order", lambda order: replace(place(order),
            costs_complete=False, cumulative_gross=None, tax=None))
    manager = OrderManager(broker, RiskManager(broker, db, config=RiskConfig(position_size_limit=.5)),
                           db, settings=settings, notifier=Mock())
    manager.sync_broker_state()
    observation = db.query(AccountObservation).one()
    observation.ref_date -= timedelta(days=1)
    observation.observed_at -= timedelta(days=1)
    db.get(ExecutionAccountState, account_key(settings)).ref_date = observation.ref_date
    db.commit()
    repo, cycle, old = seeded(db, settings)
    service = FujimotoService(db, manager, settings=settings)
    service.recover_links(cycle.account_key)
    if mode == Mode.SAFE:
        cycle = repo.create_cycle(service.current_configs(cycle.account_key)["safe"].id, "AAA")
        db.commit()
    gate = {"config_ids": {m: c.id for m, c in service.current_configs(cycle.account_key).items()},
            "fingerprint": "offline_fixture", "replay_id": 123}
    monkeypatch.setattr(FujimotoService, "validation_gate", lambda *a, **kw: gate)
    service._control(cycle.account_key, {**gate, "execution_mode": "paper", "entries_enabled": True,
        "sell_consent": True, "consent_scope": "new_fujimoto_acquisitions_only"})
    now = datetime.now(timezone.utc)
    rule = RuleEvidence(now.date() - timedelta(days=1), 1000, True,
        financial_status="maintained", daily_rsi=40, weekly_rsi=50, atr14=40)
    try:
        with account_execution_lock(cycle.account_key):
            service._submit_decision(cycle, rule, evaluate(mode, rule, CycleState()), 1000, None, now)
        state = repo.state(cycle.id)
        assert state.quantity > 0 and state.buy_stage == 1 and not state.pending_order
        if mode == Mode.SAFE:
            assert state.stop_price == 880
        assert bool(service.unresolved_costs(cycle.account_key)) is not costs_complete
        service.stop(cycle.account_key, 7)
        with account_execution_lock(cycle.account_key):
            service._submit_decision(cycle, rule, Decision("sell", "financial_deterioration",
                sell_quantity=state.quantity), 1000, None, datetime.now(timezone.utc))
        assert repo.state(cycle.id).quantity == 0
        assert broker.get_position("AAA") is None
    finally:
        release_process_locks()
