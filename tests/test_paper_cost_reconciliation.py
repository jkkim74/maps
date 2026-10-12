"""Account reconciliation and overlapping statement regressions, entirely offline."""
import datetime as dt
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

from maps.common.exceptions import ExecutionBlockedError
from maps.common.models import (
    AccountAdjustment, AccountObservation, ExecutionAccountState, ExecutionSafetyEvent,
    OrderIntent, OrderLog, PaperCostEvidence, PaperCostSettlement,
)
from maps.common.settings import MapsSettings
from maps.execution.broker_adapter import (
    AccountActivity, AccountBalance, OrderResult, OrderSide, OrderStatus, Position,
    PositionSnapshot,
)
from maps.execution.paper_costs import commit_settlement, preview_settlement
from maps.execution.reconciliation import audit_id, reconcile
from maps.execution.safety import account_key, utcnow
from tests.test_paper_costs import incident, statement


@pytest.fixture
def paper_sync(db: Session) -> SimpleNamespace:
    """Stable synthetic BUY evidence and a preserved complete accounting baseline."""
    settings = MapsSettings(_env_file=None, maps_broker_mode="kis", kis_real_trading=False,
        kis_account_no="12345678-01", maps_paper_cost_reconciliation_mode="auto")
    key, now = account_key(settings), utcnow()
    today = (now + dt.timedelta(hours=9)).date()
    evidence = {"positions": {}, "orders": {}, "costs": "0", "unsupported": [],
        "cost_observation": {"ref_date": str(today), "today_total": "0", "valid": True}}
    first = AccountObservation(account_key=key, observed_at=now-dt.timedelta(days=1),
        ref_date=today-dt.timedelta(days=1), nav=10000, cash=10000,
        evidence=evidence, complete=True)
    baseline = AccountObservation(account_key=key, observed_at=now-dt.timedelta(minutes=2),
        ref_date=today, nav=10000, cash=10000, evidence=evidence, complete=True)
    fill = OrderResult("fixture-buy", "test", "AAA", OrderSide.BUY, OrderStatus.FILLED,
        2, 1000, quantity=2, cumulative_gross=2000, submitted_at=dt.datetime.now())
    oid = audit_id(fill, settings)
    cost = {"ref_date": str(today), "today_total": "40", "valid": True}
    prior = AccountObservation(account_key=key, observed_at=now-dt.timedelta(seconds=1),
        ref_date=today, nav=9960, cash=7960, complete=False,
        evidence={**evidence, "cost_observation": cost,
            "positions": {"AAA": {"qty": 2, "value": "2000"}},
            "orders": {oid: {"ticker": "AAA", "side": "buy", "qty": 2,
                             "notional": "2000", "gross_source": "reported_total"}}})
    state = ExecutionAccountState(account_key=key, environment="kis_paper", status="BLOCKED",
        block_reasons=["account_difference_unclassified"], checked_at=now, version=3,
        killed=False, value_index=1, high_water=1, day_open_index=1, ref_date=today)
    db.add_all([first, baseline, prior, state]); db.flush()
    pending = AccountAdjustment(account_key=key, observation_id=prior.id, status="unclassified",
        amount=-40, evidence={"cash_difference": "-40", "quantity_changes": {}}, created_at=now)
    db.add(pending); db.commit()
    snapshot = PositionSnapshot({"AAA": Position("AAA", 2, 1000, current_price=1000)},
        AccountBalance(7960, 2000, 9960), dt.datetime.now(dt.timezone.utc), cost_observation=cost)
    broker = SimpleNamespace(get_order_history=lambda *args: [fill], get_open_orders=lambda: [],
        get_execution_snapshot=lambda: snapshot, get_account_activity=lambda *args: AccountActivity(True, Decimal(0)))
    return SimpleNamespace(settings=settings, key=key, baseline=baseline, prior=prior,
                           pending=pending, fill=fill, oid=oid, snapshot=snapshot, broker=broker)


@pytest.mark.parametrize("failure", ["regressed_fill", "quantity_difference", "unknown_order",
                                     "open_order", "provisional_gross", "historical_quantity_marker"])
def test_incomplete_account_never_commits_cost_receipt(db: Session, paper_sync: SimpleNamespace, failure: str) -> None:
    """The real reconciler must retain the marker and baseline before any cost mutation."""
    case = paper_sync
    if failure == "regressed_fill":
        case.baseline.cash = 7000
        case.baseline.evidence = {**case.baseline.evidence,
            "positions": {"AAA": {"qty": 3, "value": "3000"}},
            "orders": {case.oid: {"ticker": "AAA", "side": "buy", "qty": 3,
                                 "notional": "3000", "gross_source": "reported_total"}}}
        reason = "fill_quantity_regressed"
    elif failure == "quantity_difference":
        case.snapshot.positions["AAA"].quantity = 3
        reason = "account_difference_unclassified"
    elif failure == "open_order":
        case.broker.get_open_orders = lambda: [SimpleNamespace(order_id="open-fixture")]
        reason = "paper_costs_account_incomplete"
    elif failure == "provisional_gross":
        case.fill.cumulative_gross = None
        reason = "paper_costs_order_gross_missing"
    elif failure == "historical_quantity_marker":
        case.pending.evidence = {"cash_difference": "-40", "quantity_changes": {"AAA": 1}}
        reason = "quantity_difference_requires_classification"
    else:
        now = utcnow()
        db.add(OrderIntent(id="unresolved-fixture", account_key=case.key, environment="kis_paper",
            event_key="unresolved", strategy_id="test", ticker="BBB", side="buy", status="UNKNOWN",
            request={}, quantity=1, created_at=now, updated_at=now, valid_until=now+dt.timedelta(minutes=1)))
        reason = "order_outcome_unknown"
    db.commit()
    for _ in range(2):
        result, _, _ = reconcile(db, case.broker, case.settings)
        assert not result["complete"]
        assert db.query(PaperCostSettlement).count() == 0
        assert reason in result["block_reasons"]
        assert case.pending.status == "unclassified"
        assert db.query(AccountObservation).filter_by(complete=True).order_by(AccountObservation.id.desc()).first().id == case.baseline.id
        assert db.query(ExecutionSafetyEvent).filter_by(reason_code="paper_costs_auto_settled").count() == 0


def test_observe_to_auto_and_new_session_apply_cost_only_once(db: Session, paper_sync: SimpleNamespace) -> None:
    """Changing mode or restarting the DB session cannot consume a cost twice."""
    case = paper_sync
    case.settings.maps_paper_cost_reconciliation_mode = "observe"
    result, _, _ = reconcile(db, case.broker, case.settings)
    assert "paper_costs_observe" in result["block_reasons"]
    assert db.query(PaperCostSettlement).count() == 0
    assert case.pending.status == "unclassified"
    case.settings.maps_paper_cost_reconciliation_mode = "auto"
    result, _, _ = reconcile(db, case.broker, case.settings)
    assert result["complete"], result
    assert case.pending.status == "costs_resolved"
    assert Decimal(db.query(PaperCostSettlement).one().payload["deduction"]) == 40
    with Session(db.get_bind()) as restarted:
        for mode in ("auto", "observe"):
            case.settings.maps_paper_cost_reconciliation_mode = mode
            result, _, _ = reconcile(restarted, case.broker, case.settings)
            assert result["complete"], result
            assert restarted.query(PaperCostSettlement).count() == 1
            assert restarted.query(AccountAdjustment).filter_by(status="unclassified").count() == 0


def test_automatic_marker_failure_rolls_back_entire_settlement(
    db: Session, paper_sync: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even a rejection after marker changes cannot leak accounting side effects."""
    from maps.execution import paper_costs
    original = paper_costs._resolve_adjustments

    def reject_after_resolution(*args, **kwargs) -> None:
        """Inject a domain rejection after the actual marker mutation."""
        original(*args, **kwargs)
        raise ExecutionBlockedError("fixture_after_cost_resolution")

    monkeypatch.setattr(paper_costs, "_resolve_adjustments", reject_after_resolution)
    result, _, _ = reconcile(db, paper_sync.broker, paper_sync.settings)
    assert "fixture_after_cost_resolution" in result["block_reasons"]
    assert paper_sync.pending.status == "unclassified"
    assert paper_sync.pending.version == 1
    assert db.query(PaperCostSettlement).count() == 0
    assert db.query(ExecutionSafetyEvent).filter_by(reason_code="paper_costs_auto_settled").count() == 0


def _accept(db: Session, settings: MapsSettings, request: dict, identity: str) -> dict:
    """Submit a current reviewed draft with a unique synthetic request identity."""
    preview = preview_settlement(db, settings, request)
    body = {**request, "fingerprint": preview["fingerprint"], "version": preview["version"],
            "idempotency_key": identity}
    return commit_settlement(db, settings, body, "test-admin", owner_user_id=7)


@pytest.mark.parametrize("replacement", ["same", "combined", "paid"])
def test_shared_interval_includes_prior_order_costs_across_statements(db: Session, incident: tuple, replacement: str) -> None:
    """A replacement account document cannot reset the interval's allocated costs."""
    settings, _, current, _, _ = incident
    account = statement(incident)["items"][0]
    items = []
    for oid in ("sell1", "sell2"):
        trade = current.evidence["orders"][oid]
        db.add(OrderLog(account_key=current.account_key, order_id=oid, strategy_id="test",
            ticker=trade["ticker"], side=trade["side"], qty=trade["qty"], fill_qty=trade["qty"],
            status="filled", broker="kis", mode="mock"))
        items.append({**account, "scope": "order", "broker_order_id": oid,
            "amount": "200", "fees": "200", "tax": "0", "gross": trade["notional"]})
    db.commit()
    receipt = _accept(db, settings, {"reason": "Actual statements reviewed", "items": [account, items[0]]}, "first")
    if replacement != "same":
        refreshed = {**account, "document_hash": "b"*64,
                     "included_settlement_ids": [db.query(PaperCostSettlement).one().id]}
        if replacement == "paid":
            receipt = _accept(db, settings, {"reason": "Replacement statement reviewed",
                                            "items": [refreshed]}, "replacement")
            request_items = [{**items[1], "account_evidence_id": receipt["evidence_ids"][0]}]
        else:
            request_items = [refreshed, items[1]]
    else:
        request_items = [{**items[1], "account_evidence_id": receipt["evidence_ids"][0]}]
    before = db.query(PaperCostEvidence).count()
    with pytest.raises(ExecutionBlockedError, match="order_cost_exceeds_account_total"):
        preview_settlement(db, settings, {"reason": "More actual order evidence", "items": request_items})
    assert db.query(PaperCostEvidence).count() == before
    assert db.query(PaperCostSettlement).count() == (2 if replacement == "paid" else 1)
    request_items[-1].update(amount="160", fees="160")
    accepted = _accept(db, settings, {"reason": "Exact remaining order cost reviewed",
                                    "items": request_items}, "within-limit")
    assert Decimal(accepted["deduction"]) == 0
