"""Offline actual-evidence-shaped fixtures for bounded KIS paper authorization."""
import base64
import json
import zlib
from datetime import date, datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from maps.common.exceptions import ExecutionBlockedError
from maps.common.models import AccountObservation, ExecutionAccountState, FujimotoCycle, FujimotoEvidence, ValidationRun, PromotionHistory
from maps.common.settings import MapsSettings
from maps.execution.safety import account_key
from maps.fujimoto.domain import Mode, CycleState, evaluate
from maps.fujimoto.repository import json_data
from maps.fujimoto.service import FujimotoService

NOW = datetime(2026, 10, 8, 1, tzinfo=timezone.utc)


@pytest.fixture
def db():
    """Share one isolated SQLite connection across actual API worker threads."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool
    from maps.common.db import Base
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.fixture
def ready(db, monkeypatch):
    """Persist source snapshots and actual-feed-shaped observations, never contact KIS."""
    import maps.fujimoto.service as module
    from maps.fujimoto.replay import screening_evidence
    from maps.market.trading_rules import previous_trading_day
    monkeypatch.setattr(module, "utcnow", lambda: NOW.replace(tzinfo=None))
    settings = MapsSettings(_env_file=None, maps_broker_mode="kis", kis_real_trading=False,
        maps_fujimoto_enabled=True, maps_live_trading_enabled=True, maps_dry_run=False)
    service = FujimotoService(db, Mock(), settings=settings)
    key = account_key(settings)
    service.configure(key, 7, 10000000)
    stamp = NOW.replace(tzinfo=None)
    db.add(ExecutionAccountState(account_key=key, environment="kis_paper", status="READY",
        block_reasons=[], killed=False, checked_at=stamp, last_complete_at=stamp))
    db.add(AccountObservation(account_key=key, observed_at=stamp, ref_date=NOW.date(),
        nav=100000000, cash=100000000, complete=True,
        evidence={"positions": {}, "orders": {}, "costs": "0", "unsupported": []}))
    day = date(2026, 10, 7)
    dates = [day]
    for _ in range(450):
        dates.append(previous_trading_day(dates[-1]))
    annual = [dict(ticker="005930", period_end=f"{y}-12-31", receipt=str(y),
        publication_date=f"{y+1}-03-20", first_observed_at=f"{y+1}-03-20T00:00:00",
        available_date=f"{y+1}-03-24", basis="CFS", currency="KRW", share_basis="verified",
        revenue=v, operating_profit=v/10, dividend_per_share=v/100)
        for y, v in ((2023, 100), (2024, 110), (2025, 120))]
    raw = dict(annual_records=annual, financial_records=[dict(ticker="005930",
        period_end="2026-06-30", receipt="q", publication_date="2026-08-14",
        first_observed_at="2026-08-14T00:00:00", available_date="2026-08-18", basis="CFS",
        currency="KRW", revenue=120, prior_revenue=100, operating_profit=12, prior_operating_profit=10)],
        valuations=[dict(ticker="005930", ref_date=str(day), available_at=f"{day}T07:00:00", per=10)],
        sectors=dict(ref_date=str(day), available_at=f"{day}T07:00:00", memberships=[["005930", "s"]]),
        prices=[dict(date=str(d), open=1000, high=1010, low=990, close=1000,
            volume=100000, turnover=100000000) for d in reversed(dates)], eligible=True)
    rule = screening_evidence(raw, "005930", day)
    assert rule.selection_passed and rule.financial_status == "maintained"
    source_at = datetime(2026, 10, 7, 13)
    universe = service.repo.record_evidence("universe", "*", source_at, source_at,
        {"ref_date": str(day), "members": [{"ticker": "005930", "eligible": True}]})
    candidate = service.repo.record_evidence("candidate", "005930", source_at, source_at,
        {"ref_date": str(day), "universe_id": universe.id, "sector_run_id": 1, "rule": rule,
         "raw_zlib": base64.b64encode(zlib.compress(json.dumps(raw).encode())).decode()})
    service.repo.record_evidence("screen", "*", source_at, source_at,
        {"ref_date": str(day), "screened": 1, "selected": 1, "ranked": ["005930"], "universe_id": universe.id})
    service.feed.record_subscriptions(["005930"], now=NOW-timedelta(seconds=10))
    from maps.limit_up.feed import FeedQuote
    service.feed.on_quote(FeedQuote("005930", 1001, 100, 1000, 300, 1., 100, 300, NOW, NOW), now=NOW)
    db.commit()
    return service, key, candidate, rule


def test_activation_without_replay_is_scheduled_and_never_promotes(ready, db):
    service, key, _, rule = ready
    assert all(evaluate(mode, rule, CycleState()).action != "buy" for mode in Mode)
    control = service.activate_paper_test(key, 7, sell_consent=True)
    assert control["authorization_policy"] == "paper_test"
    assert control["starts_at"] == "2026-10-12T09:00:00+09:00"
    assert control["expires_at"] == "2026-11-06T15:20:00+09:00"
    assert service.paper_test_status(key)["state"] == "scheduled"
    assert db.query(ValidationRun).count() == db.query(PromotionHistory).count() == 0
    with pytest.raises(ExecutionBlockedError, match="before_start"):
        service.authorize_order(key, buy=True, now=NOW, ticker="005930")
    assert service.control(key)["entries_enabled"] is True


@pytest.mark.parametrize("change,reason", [
    ({"maps_broker_mode": "mock"}, "paper_environment"),
    ({"kis_real_trading": True}, "paper_environment"),
    ({"kis_account_no": "11111111-01"}, "account_identity"),
    ({"maps_fujimoto_enabled": False}, "runtime_disabled"),
    ({"maps_dry_run": True}, "dry_run"),
])
def test_trial_activation_rejects_environment_and_switches(ready, change, reason):
    service, key, _, _ = ready
    service.settings = service.settings.model_copy(update=change)
    with pytest.raises(ExecutionBlockedError, match=reason):
        service.activate_paper_test(key, 7, sell_consent=True)


def test_trial_requires_owner_consent_and_exact_budget(ready):
    service, key, _, _ = ready
    for owner, consent, reason in ((8, True, "owner"), (7, False, "consent")):
        with pytest.raises(ExecutionBlockedError, match=reason):
            service.activate_paper_test(key, owner, sell_consent=consent)
    service.configure(key, 7, 10000002, deposit=2)
    with pytest.raises(ExecutionBlockedError, match="budget"):
        service.activate_paper_test(key, 7, sell_consent=True)


@pytest.mark.parametrize("kind", ["quote", "screen", "candidate", "universe", "account", "recording", "financial"])
def test_missing_genuine_observations_block_readiness_and_activation(ready, db, kind):
    service, key, candidate, _ = ready
    if kind == "account":
        db.query(ExecutionAccountState).first().status = "BLOCKED"
        db.query(ExecutionAccountState).first().block_reasons = ["account_difference_unclassified"]
    elif kind == "financial":
        # New genuinely incomplete source snapshot masks the older good candidate.
        payload = dict(candidate.payload)
        payload["rule"] = {**payload["rule"], "financial_status": "missing"}
        service.repo.record_evidence("candidate", candidate.ticker, NOW, NOW, payload)
    else:
        # SQL fixture deletion avoids repository immutable-history ORM protection.
        from sqlalchemy import delete
        db.execute(delete(FujimotoEvidence).where(FujimotoEvidence.kind == ("feed_recording" if kind == "recording" else kind)))
    db.commit()
    assert service.paper_test_status(key)["eligible"] is False
    with pytest.raises(ExecutionBlockedError):
        service.activate_paper_test(key, 7, sell_consent=True)


def test_status_is_read_only_and_stale_observations_fail(ready, db):
    service, key, _, _ = ready
    before = db.query(FujimotoEvidence).count()
    assert service.paper_test_status(key, now=NOW)["eligible"]
    assert not service.paper_test_status(key, now=NOW+timedelta(seconds=31))["eligible"]
    assert db.query(FujimotoEvidence).count() == before
    service.manager.assert_not_called()


def test_config_and_code_binding_block_buy_and_normal_policy_restores(ready, monkeypatch):
    service, key, _, _ = ready
    service.activate_paper_test(key, 7, sell_consent=True)
    start = datetime(2026, 10, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("maps.fujimoto.replay.code_fingerprint", lambda: "changed")
    with pytest.raises(ExecutionBlockedError, match="binding"):
        service.authorize_order(key, buy=True, now=start)
    # Normal activation must explicitly replace the policy rather than merge it.
    gate = {"config_ids": {m: c.id for m, c in service.current_configs(key).items()}}
    monkeypatch.setattr(service, "validation_gate", lambda *a, **kw: gate)
    result = service.activate(key, 7, execution_mode="paper", replay_id=1, sell_consent=True)
    assert result["authorization_policy"] == "validated"


def pending_buy(service, key, candidate, *, status="PARTIAL"):
    """An explicit ledger fixture tests reservations, not actual broker execution."""
    from maps.fujimoto.domain import Decision
    from maps.fujimoto.repository import FillEvent
    cycle = service.repo.create_cycle(service.current_configs(key)["original"].id, candidate.ticker)
    decision = Decision("buy", "fixture", buy_stage=1, buy_weight=1, price_cap=1000, timing="next_session")
    order = service.repo.reserve_order(cycle.id, decision, candidate.id, 2, 1000, signal_date=date(2026, 10, 7))
    service.repo.apply_fill(FillEvent(order.id, key, None, 1, 1000, 1, 0, status, NOW.date()))
    order.broker_order_id = "known"
    service.db.commit()
    return cycle, order


@pytest.mark.parametrize("status", ["PARTIAL", "UNKNOWN", "CANCEL_REQUESTED"])
@pytest.mark.parametrize("end", ["stop", "expiry", "storage"])
def test_trial_stop_expiry_capacity_preserve_partial_unknown_and_cancel_once(ready, db, status, end):
    service, key, candidate, _ = ready
    control = service.activate_paper_test(key, 7, sell_consent=True)
    cycle, order = pending_buy(service, key, candidate, status=status)
    if end == "stop":
        service.stop(key, 7)
    elif end == "storage":
        service.settings = service.settings.model_copy(update={"maps_fujimoto_tape_rows": 1})
    when = datetime.fromisoformat(control["expires_at"]) if end == "expiry" else NOW
    service.manager.sync_broker_state = Mock()
    # Already-linked truth is represented by this isolated fixture; recovery does no send.
    service.tick(now=when)
    reboot = FujimotoService(db, service.manager, settings=service.settings)
    reboot.tick(now=when)
    assert service.control(key)["entries_enabled"] is False
    assert service.control(key)["authorization_id"] == control["authorization_id"]
    assert service.control(key)["expires_at"] == control["expires_at"]
    assert service.repo.state(cycle.id).quantity == 1 and service.repo.state(cycle.id).pending_order
    assert service.repo.reserved_cash(cycle.config_id) > 1000
    assert service.manager.cancel.call_count == (1 if status == "PARTIAL" else 0)
    assert order.status == ("CANCEL_REQUESTED" if status == "PARTIAL" else status)
    with pytest.raises(ExecutionBlockedError, match="ownership_or_orders"):
        reboot.activate_paper_test(key, 7, sell_consent=True)


def test_storage_exhaustion_does_not_block_paper_exit_permission(ready):
    service, key, _, _ = ready
    control = service.activate_paper_test(key, 7, sell_consent=True)
    service.settings = service.settings.model_copy(update={"maps_fujimoto_tape_rows": 1})
    assert service.authorize_order(key, buy=False, now=NOW)["authorization_id"] == control["authorization_id"]
    assert not service.control(key)["entries_enabled"]
    service.settings = service.settings.model_copy(update={"kis_real_trading": True})
    with pytest.raises(ExecutionBlockedError):
        service.authorize_order(key, buy=False, now=NOW)


def test_stopped_unowned_campaign_can_restart_but_never_renews_on_read(ready, db):
    service, key, _, _ = ready
    old = service.activate_paper_test(key, 7, sell_consent=True)
    with pytest.raises(ExecutionBlockedError, match="already_authorized"):
        service.activate_paper_test(key, 7, sell_consent=True)
    service.stop(key, 7)
    before = db.query(FujimotoEvidence).count()
    assert service.paper_test_status(key)["state"] == "stopped"
    assert db.query(FujimotoEvidence).count() == before
    service.configure(key, 7, 10000000)
    new = service.activate_paper_test(key, 7, sell_consent=True)
    assert new["authorization_id"] != old["authorization_id"]


def test_session20_uses_configured_closures_and_expires_exactly(ready):
    service, key, _, _ = ready
    service.settings = service.settings.model_copy(update={"maps_krx_closed_dates": "2026-10-12"})
    control = service.activate_paper_test(key, 7, sell_consent=True)
    assert control["starts_at"] == "2026-10-13T09:00:00+09:00"
    assert control["expires_at"] == "2026-11-09T15:20:00+09:00"
    expiry = datetime.fromisoformat(control["expires_at"])
    assert service.paper_test_status(key, now=expiry-timedelta(microseconds=1))["state"] == "active"
    assert service.paper_test_status(key, now=expiry)["state"] == "expired"
    assert service.control(key)["entries_enabled"]
    with pytest.raises(ExecutionBlockedError, match="entries_stopped"):
        service.authorize_order(key, buy=True, now=expiry)
    assert not service.control(key)["entries_enabled"]


def trial_buy_source(ready):
    """Use raw prices to produce a genuine natural first BUY in a source-check fixture."""
    from maps.fujimoto.replay import screening_evidence
    service, key, candidate, _ = ready
    control = service.activate_paper_test(key, 7, sell_consent=True)
    # Source-validator tests start an internal persisted campaign at fixture time.
    control = service._control(key, {**control, "starts_at": NOW.isoformat()})
    raw = json.loads(zlib.decompress(base64.b64decode(candidate.payload["raw_zlib"])))
    raw["prices"][-1]["close"] = 990
    rule = screening_evidence(raw, candidate.ticker, date(2026, 10, 7))
    payload = {**candidate.payload, "rule": rule,
        "raw_zlib": base64.b64encode(zlib.compress(json.dumps(raw).encode())).decode()}
    candidate = service.repo.record_evidence("candidate", candidate.ticker, NOW, NOW, payload)
    cycle = service.repo.create_cycle(service.current_configs(key)["original"].id, candidate.ticker)
    decision = evaluate(Mode.ORIGINAL, rule, CycleState(), decision_date=NOW.date())
    assert decision.action == "buy"
    source = service.repo.record_evidence("execution_decision", candidate.ticker, NOW, NOW,
        {"rule": rule, "decision": decision, "candidate_id": candidate.id,
         "approval_id": control["control_id"], "authorization_id": control["authorization_id"]}, account_key=key)
    reservation = service.repo.reserve_order(cycle.id, decision, source.id, 2, 990, signal_date=rule.as_of)
    service.db.commit()
    from maps.execution.broker_adapter import Order, OrderSide, OrderType
    from maps.execution.safety import ExecutionContext
    order = Order(Mode.ORIGINAL.strategy_id, candidate.ticker, OrderSide.BUY, OrderType.LIMIT, 2, 990)
    context = ExecutionContext(f"fujimoto:{reservation.id}", source="fujimoto", source_id=cycle.id)
    return service, key, source, reservation, order, context


def test_trial_buy_source_accepts_exact_natural_source(ready, db):
    from maps.fujimoto.service import validate_source
    service, key, source, _, order, context = trial_buy_source(ready)
    assert validate_source(db, order, context, service.settings) == source.payload["approval_id"]


@pytest.mark.parametrize("field", ["rule", "decision", "candidate_id", "authorization_id"])
def test_trial_buy_source_rejects_forged_evidence(ready, db, field):
    from maps.fujimoto.service import validate_source
    service, key, source, reservation, order, context = trial_buy_source(ready)
    payload = dict(source.payload)
    if field == "rule":
        payload[field] = {**payload[field], "daily_rsi": 5}
    elif field == "decision":
        payload[field] = {**payload[field], "reason": "forged"}
        reservation.decision = payload[field]
    else:
        payload[field] = 999999 if field == "candidate_id" else "forged"
    forged = service.repo.record_evidence("execution_decision", order.ticker, NOW, NOW, payload, account_key=key)
    reservation.evidence_id = forged.id
    db.commit()
    with pytest.raises(ExecutionBlockedError, match="source_mismatch"):
        validate_source(db, order, context, service.settings)


def test_trial_sell_retains_owned_consent_after_expiry_and_storage_exhaustion(ready, db):
    from maps.common.models import OrderIntent
    from maps.fujimoto.domain import Decision
    from maps.fujimoto.repository import FillEvent
    from maps.fujimoto.service import validate_source
    from maps.execution.broker_adapter import Order, OrderSide, OrderType
    from maps.execution.safety import ExecutionContext
    service, key, source, reservation, buy, context = trial_buy_source(ready)
    cycle = db.get(FujimotoCycle, context.source_id)
    stamp = NOW.replace(tzinfo=None)
    intent = OrderIntent(id="trial-fill", account_key=key, environment="kis_paper", event_key=context.event_key,
        strategy_id=buy.strategy_id, ticker=buy.ticker, side="buy", status="FILLED", quantity=2, filled_quantity=2,
        reserved_amount=0, reserved_quantity=0, request={"source": "fujimoto", "source_id": cycle.id,
            "fujimoto_approval_id": source.payload["approval_id"]}, valid_until=stamp+timedelta(seconds=3),
        created_at=stamp, updated_at=stamp, broker_order_id="filled")
    db.add(intent)
    db.flush()
    service.repo.bind_intent(reservation.id, intent.id, "filled")
    service.repo.apply_fill(FillEvent(reservation.id, key, intent.id, 2, 1980, 1, 0, "FILLED", NOW.date()))
    sell = service.repo.reserve_order(cycle.id, Decision("sell", "protective", sell_quantity=2), source.id, 2, 990)
    db.commit()
    service.settings = service.settings.model_copy(update={"maps_fujimoto_tape_rows": 1})
    service.authorize_order(key, buy=False, now=datetime.fromisoformat(service.control(key)["expires_at"]))
    ctx = ExecutionContext(f"fujimoto:{sell.id}", source="fujimoto", source_id=cycle.id)
    order = Order(buy.strategy_id, buy.ticker, OrderSide.SELL, OrderType.LIMIT, 2, 990)
    assert validate_source(db, order, ctx, service.settings)
    assert service.repo.state(cycle.id).quantity == 2 and not service.control(key)["entries_enabled"]
    with pytest.raises(ExecutionBlockedError):
        validate_source(db, order, ctx, service.settings.model_copy(update={"kis_real_trading": True}))


def test_scheduled_quote_keeps_permission_and_does_not_create_buy_cycles(ready, monkeypatch):
    service, key, _, _ = ready
    old = service.activate_paper_test(key, 7, sell_consent=True)
    monkeypatch.setattr(service, "_submit_decision", lambda *a: pytest.fail("scheduled order"))
    from maps.limit_up.feed import FeedQuote
    now = NOW+timedelta(seconds=1)
    service.on_quote(FeedQuote("005930", 1001, 100, 1000, 300, 1., 100, 300, now, now), now=now)
    assert not service.repo.cycles(key)
    assert service.control(key)["entries_enabled"]
    assert service.control(key)["starts_at"] == old["starts_at"]


def test_new_config_blocks_old_trial_until_explicit_new_approval(ready):
    service, key, _, _ = ready
    service.activate_paper_test(key, 7, sell_consent=True)
    service.configure(key, 7, 10000000, with_orderbook=False)
    with pytest.raises(ExecutionBlockedError, match="binding_changed"):
        service.authorize_order(key, buy=True, now=NOW)
    new = service.activate_paper_test(key, 7, sell_consent=True)
    assert new["config_ids"] == {m: c.id for m, c in service.current_configs(key).items()}


def test_ready_trial_api_activates_without_replay_and_status_is_read_only(ready, db):
    from fastapi.testclient import TestClient
    from maps.api.fujimoto import scoped
    import main
    service, key, _, _ = ready
    main.app.dependency_overrides[scoped] = lambda: (service, key, 7)
    try:
        client = TestClient(main.app)
        response = client.post("/api/v1/fujimoto/activate-paper-test", json={"sell_consent": True})
        assert response.status_code == 200 and response.json()["authorization_policy"] == "paper_test"
        before = db.query(FujimotoEvidence).count()
        body = client.get("/api/v1/fujimoto/status").json()
        assert body["paper_test"]["state"] == "scheduled"
        assert body["paper_test"]["authorization_id"] == response.json()["authorization_id"]
        assert db.query(FujimotoEvidence).count() == before
    finally:
        main.app.dependency_overrides.clear()
