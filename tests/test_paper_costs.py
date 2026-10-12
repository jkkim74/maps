"""Actual evidence, not cash residuals or fee rates, authorizes paper costs."""
import datetime as dt
from decimal import Decimal
from types import SimpleNamespace

import pytest

from maps.common.exceptions import ExecutionBlockedError
from maps.common.models import AccountAdjustment, AccountObservation, ExecutionAccountState
from maps.common.settings import MapsSettings
from maps.execution.safety import account_key, utcnow


@pytest.fixture
def incident(db):
    """Synthetic arithmetic from three fully recorded paper fills."""
    settings = MapsSettings(maps_broker_mode="kis", kis_real_trading=False,
                            kis_account_no="12345678-01", maps_paper_cost_reconciliation_mode="observe")
    key = account_key(settings)
    now = utcnow()
    base = AccountObservation(account_key=key, observed_at=now-dt.timedelta(days=5),
        ref_date=(now-dt.timedelta(days=5)).date(), nav=120000000, cash=100000000,
        evidence={"positions": {}, "orders": {}, "costs": "0", "unsupported": []}, complete=True)
    db.add(base); db.flush()
    old = AccountObservation(account_key=key, observed_at=now-dt.timedelta(days=4),
        ref_date=(now-dt.timedelta(days=4)).date(), nav=120000000, cash=99999920,
        evidence={"positions": {}, "orders": {}, "costs": "0", "unsupported": []}, complete=False)
    db.add(old); db.flush()
    fee = AccountAdjustment(account_key=key, observation_id=old.id, status="confirmed", kind="fee",
        amount=-80, evidence={"cash_difference": "-80", "quantity_changes": {}},
        resolution={"source": "legacy confirmation"}, created_at=old.observed_at, version=2)
    orders = {"buy": {"ticker": "AAA", "side": "buy", "qty": 30, "notional": "6000000"},
        "sell1": {"ticker": "AAA", "side": "sell", "qty": 30, "notional": "6150000"},
        "sell2": {"ticker": "BBB", "side": "sell", "qty": 5, "notional": "8250000"}}
    base.evidence = {**base.evidence, "positions": {"BBB": {"qty": 5, "value": "8500000"}}}
    current = AccountObservation(account_key=key, observed_at=now, ref_date=now.date(),
        nav=120000000, cash=108399640, evidence={"positions": {}, "orders": orders,
        "costs": "0", "unsupported": ["paper_cost_history", "cash_transfers", "security_transfers"]}, complete=False)
    db.add_all([current, fee]); db.flush()
    pending = AccountAdjustment(account_key=key, observation_id=current.id, status="unclassified", kind=None,
        amount=-120, evidence={"cash_difference": "-120", "quantity_changes": {}}, created_at=now, version=1)
    state = ExecutionAccountState(account_key=key, environment="kis_paper", status="BLOCKED",
        block_reasons=["account_difference_unclassified"], checked_at=now, last_complete_at=base.observed_at,
        version=5, killed=False, value_index=1,high_water=1,day_open_index=1,ref_date=base.ref_date)
    db.add_all([pending, state]); db.commit()
    return settings, base, current, fee, pending


def statement(incident):
    """The document covers the interval, including the already confirmed 80."""
    _, base, current, fee, _ = incident
    return {"reason": "Broker statement reviewed for all interval trades", "items": [{
        "scope": "account", "period_start": str(base.ref_date), "period_end": str(current.ref_date),
        "amount": "360", "source_url": "https://broker.example/statement/42",
        "document_hash": "a"*64, "included_adjustment_ids": [fee.id]}]}


def test_latest_residual_not_first_pending_amount(db, incident):
    from maps.execution.paper_costs import settlement_overview
    overview = settlement_overview(db, incident[0])
    assert Decimal(overview["breakdown"]["residual"]) == Decimal(-280)
    assert Decimal(overview["breakdown"]["expected_cash"]) == Decimal(108399920)
    assert overview["breakdown"]["quantity_changes"] == {}


def test_incident_atomic_settlement_idempotency_and_no_double_fee(db, incident):
    from maps.execution.paper_costs import preview_settlement, commit_settlement, settlement_overview
    settings, _, _, fee, pending = incident
    request = statement(incident)
    preview = preview_settlement(db, settings, request)
    assert preview["can_commit"] is True
    assert Decimal(preview["deduction"]) == Decimal(280)
    assert Decimal(preview["remaining_residual"]) == 0
    request.update(fingerprint=preview["fingerprint"], version=preview["version"], idempotency_key="incident-one")
    result = commit_settlement(db, settings, request, "admin", owner_user_id=7)
    assert commit_settlement(db, settings, request, "admin", owner_user_id=7) == result
    assert Decimal(settlement_overview(db, settings)["breakdown"]["residual"]) == 0
    assert fee.amount == -80 and fee.status == "confirmed"
    assert pending.status == "costs_resolved"
    assert db.get(ExecutionAccountState, account_key(settings)).status == "BLOCKED"


def test_preview_accepts_timestamp_only_refresh_but_rejects_changed_cash(db, incident):
    from maps.execution.paper_costs import preview_settlement, commit_settlement
    settings, _, current, _, _ = incident
    request = statement(incident); preview = preview_settlement(db, settings, request)
    request.update(fingerprint=preview["fingerprint"], version=preview["version"], idempotency_key="stale")
    current.cash -= 10; db.commit()
    with pytest.raises(ExecutionBlockedError, match="settlement_changed"):
        commit_settlement(db, settings, request, "admin", owner_user_id=7)


@pytest.mark.parametrize("change", [{"document_hash": ""}, {"source_url": ""}, {"amount": "NaN"},
                                  {"amount": "-1"}, {"included_adjustment_ids": []}])
def test_missing_or_overlapping_evidence_never_clears_account(db, incident, change):
    from maps.execution.paper_costs import preview_settlement
    request = statement(incident); request["items"][0].update(change)
    with pytest.raises((ExecutionBlockedError, ValueError)):
        preview_settlement(db, incident[0], request)
    assert incident[-1].status == "unclassified"


def test_known_total_never_invents_per_order_fees(db, incident):
    from maps.execution.paper_costs import preview_settlement
    request = statement(incident); request["items"][0]["scope"] = "order"
    request["items"][0]["broker_order_id"] = "sell1"
    with pytest.raises(ExecutionBlockedError, match="order_cost"):
        preview_settlement(db, incident[0], request)


def test_timestamp_only_refresh_keeps_preview_valid(db, incident):
    from maps.execution.paper_costs import preview_settlement, commit_settlement
    settings, _, current, _, _ = incident
    request = statement(incident); preview = preview_settlement(db, settings, request)
    request.update(fingerprint=preview["fingerprint"], version=preview["version"], idempotency_key="timestamp")
    fresh = AccountObservation(account_key=current.account_key, observed_at=utcnow(), ref_date=current.ref_date,
        cash=current.cash, nav=current.nav+500, evidence=current.evidence, complete=False)
    db.add(fresh); db.get(ExecutionAccountState, current.account_key).version += 1; db.commit()
    assert commit_settlement(db, settings, request, "admin", owner_user_id=7)["status"] == "awaiting_reconciliation"


def test_auto_requires_actual_total_and_two_stable_observations(db, incident):
    from maps.execution.paper_costs import reconcile_costs
    settings, baseline, current, fee, pending = incident
    settings = settings.model_copy(update={"maps_paper_cost_reconciliation_mode": "auto"})
    db.delete(fee); db.delete(pending)
    baseline.ref_date = current.ref_date
    baseline.observed_at = current.observed_at-dt.timedelta(minutes=2)
    baseline.evidence = {**baseline.evidence, "cost_observation": {
        "source":"kis_balance","ref_date":str(current.ref_date),"today_total":"0","previous_total":None,"valid":True}}
    observation = {"source":"kis_balance","ref_date":str(current.ref_date),"today_total":"360","previous_total":None,"valid":True}
    current.evidence = {**current.evidence, "cost_observation":observation,
        "orders":{k:{**v,"gross_source":"reported_total"} for k,v in current.evidence["orders"].items()}}
    db.commit()
    result = reconcile_costs(db,settings,baseline,current,[],[])
    assert result["coverage"]["complete"] is False
    assert result["breakdown"]["settled_costs"] == "0"
    fresh = AccountObservation(account_key=current.account_key, observed_at=current.observed_at+dt.timedelta(seconds=60),
        ref_date=current.ref_date,cash=current.cash,nav=current.nav,evidence=current.evidence,complete=False)
    db.add(fresh); db.flush()
    result = reconcile_costs(db,settings,baseline,fresh,[],[])
    assert result["coverage"]["complete"] is True
    assert Decimal(result["breakdown"]["residual"]) == 0
    assert Decimal(result["breakdown"]["settled_costs"]) == 360
    db.flush()
    assert Decimal(reconcile_costs(db,settings,baseline,fresh,[],[])["breakdown"]["settled_costs"]) == 360


@pytest.mark.parametrize("mode,total,reason", [("observe","360","paper_costs_observe"),
    ("auto",None,"paper_costs_missing"),("auto","0","paper_costs_cash_mismatch"),
    ("auto","-1","paper_costs_missing")])
def test_auto_never_uses_residual_as_cost(db, incident, mode, total, reason):
    from maps.execution.paper_costs import reconcile_costs
    settings, baseline, current, fee, pending = incident
    settings=settings.model_copy(update={"maps_paper_cost_reconciliation_mode":mode})
    db.delete(fee); db.delete(pending)
    baseline.ref_date=current.ref_date; baseline.observed_at=current.observed_at-dt.timedelta(minutes=3)
    baseline.evidence={**baseline.evidence,"cost_observation":{"ref_date":str(current.ref_date),"today_total":"0","valid":True}}
    current.evidence={**current.evidence,"cost_observation":{"ref_date":str(current.ref_date),"today_total":total,"valid":total is not None},
        "orders":{k:{**v,"gross_source":"reported_total"} for k,v in current.evidence["orders"].items()}}
    prior=AccountObservation(account_key=current.account_key, observed_at=current.observed_at-dt.timedelta(seconds=60),
        ref_date=current.ref_date,cash=current.cash,nav=current.nav,evidence=current.evidence,complete=False)
    db.add(prior);db.commit()
    # Distinct timestamp ordering, not IDs, defines stability.
    result=reconcile_costs(db,settings,baseline,current,[],[])
    assert result["coverage"]["reason"] == reason
    assert Decimal(result["breakdown"]["settled_costs"]) == 0


@pytest.mark.parametrize("mode", ["observe", "auto"])
def test_manual_settlement_survives_next_sync_without_reopening_marker(db, incident, mode):
    from maps.execution.paper_costs import preview_settlement, commit_settlement
    from maps.execution.reconciliation import reconcile
    from maps.execution.broker_adapter import AccountActivity, PositionSnapshot, AccountBalance, OrderResult, OrderSide, OrderStatus
    settings, baseline, current, _, _ = incident
    settings=settings.model_copy(update={"maps_paper_cost_reconciliation_mode":mode})
    request=statement(incident); preview=preview_settlement(db,settings,request)
    request.update(fingerprint=preview["fingerprint"],version=preview["version"],idempotency_key="sync")
    commit_settlement(db,settings,request,"admin",owner_user_id=7)
    # Account IDs in the fixture must be exact broker identities used by reconcile.
    from maps.execution.reconciliation import audit_id
    fills=[OrderResult(str(i),"test",ticker,side,OrderStatus.FILLED,qty,price,
        quantity=qty,cumulative_gross=qty*price,submitted_at=dt.datetime.now()) for i,ticker,side,qty,price in
        [(1,"AAA",OrderSide.BUY,30,200000),(2,"AAA",OrderSide.SELL,30,205000),(3,"BBB",OrderSide.SELL,5,1650000)]]
    # Reuse the original semantic order keys so the receipt covers exactly this snapshot.
    for fill,key in zip(fills,current.evidence["orders"]):
        fill.order_id=key
    broker=SimpleNamespace(get_order_history=lambda *a:fills,get_open_orders=lambda:[],
        get_execution_snapshot=lambda:PositionSnapshot({},AccountBalance(float(current.cash),0,float(current.nav)),dt.datetime.now(dt.timezone.utc)),
        get_account_activity=lambda *a:AccountActivity(True,Decimal(0)))
    # The test uses the real reconciler with a fixed audit identity, no broker network.
    from unittest.mock import patch
    with patch("maps.execution.reconciliation.audit_id",side_effect=lambda result,*a:result.order_id):
        summary,_,_=reconcile(db,broker,settings)
    assert "account_difference_unclassified" not in summary["block_reasons"]
    assert db.query(AccountAdjustment).filter_by(status="unclassified").count()==0
    assert summary["complete"], summary
    latest=db.query(AccountObservation).order_by(AccountObservation.id.desc()).first()
    assert latest.evidence["paper_costs"]["complete"]


def test_legacy_fee_classification_requires_new_statement_flow(db, incident):
    from maps.execution.safety_admin import classify_adjustment
    request = SimpleNamespace(version=1, kind="fee", amount=-120, quantity_changes={},
        evidence={"owner_confirmed": True}, reason="Legacy assertion must not settle costs")
    with pytest.raises(ExecutionBlockedError, match="paper_cost_statement_required"):
        classify_adjustment(db, incident[0], incident[-1].id, request, "admin")


def test_order_allocations_cannot_exceed_account_statement(db, incident):
    from maps.common.models import OrderLog
    from maps.execution.paper_costs import preview_settlement
    request = statement(incident)
    for oid in ("sell1", "sell2"):
        trade = incident[2].evidence["orders"][oid]
        db.add(OrderLog(account_key=incident[2].account_key, order_id=oid, strategy_id="test",
            ticker=trade["ticker"], side=trade["side"], qty=trade["qty"], fill_qty=trade["qty"],
            status="filled", broker="kis", mode="mock"))
        request["items"].append({**request["items"][0], "scope":"order", "broker_order_id":oid,
            "amount":"200", "fees":"200", "tax":"0", "gross":trade["notional"]})
    db.commit()
    with pytest.raises(ExecutionBlockedError, match="order_cost_exceeds_account_total"):
        preview_settlement(db, incident[0], request)


def test_historical_order_costs_after_baseline_rollover_do_not_deduct_cash_twice(db, incident):
    from maps.common.models import OrderLog
    from maps.execution.paper_costs import preview_settlement, commit_settlement, settlement_overview
    settings, _, current, _, _ = incident
    request = statement(incident)
    preview = preview_settlement(db, settings, request)
    request.update(fingerprint=preview["fingerprint"],version=preview["version"],idempotency_key="account")
    receipt = commit_settlement(db, settings, request, "admin", owner_user_id=7)
    trade = current.evidence["orders"]["sell1"]
    db.add(OrderLog(account_key=current.account_key, order_id="sell1", strategy_id="test",
        ticker=trade["ticker"],side="sell",qty=30,fill_qty=30,status="filled",broker="kis",mode="mock"))
    fresh = AccountObservation(account_key=current.account_key,observed_at=utcnow(),ref_date=current.ref_date+dt.timedelta(days=1),
        cash=current.cash,nav=current.nav,complete=True,
        evidence={"positions":{},"orders":{},"costs":"0","unsupported":[]})
    db.add(fresh);db.commit()
    candidate = next(c for c in settlement_overview(db,settings)["order_cost_candidates"] if c["broker_order_id"]=="sell1")
    item={**statement(incident)["items"][0],"scope":"order","broker_order_id":"sell1",
        "account_evidence_id":receipt["evidence_ids"][0],"gross":trade["notional"],
        "fees":"20","tax":"100","amount":"120"}
    order_request={"items":[item],"reason":"Actual broker order statement verified"}
    preview=preview_settlement(db,settings,order_request)
    assert Decimal(preview["deduction"])==0
    assert candidate["account_evidence_id"]==item["account_evidence_id"]
    order_request.update(fingerprint=preview["fingerprint"],version=preview["version"],idempotency_key="order")
    commit_settlement(db,settings,order_request,"admin",owner_user_id=7)
    assert Decimal(settlement_overview(db,settings)["breakdown"]["residual"])==0
    from maps.execution.paper_costs import reconcile_costs
    later=AccountObservation(account_key=fresh.account_key,observed_at=fresh.observed_at+dt.timedelta(seconds=30),
        ref_date=fresh.ref_date,cash=fresh.cash,nav=fresh.nav,evidence=fresh.evidence,complete=False)
    db.add(later);db.flush()
    assert not reconcile_costs(db,settings,fresh,later,[],[])["coverage"]["complete"]


def test_fujimoto_cost_and_account_receipt_rollback_together(db, incident, monkeypatch):
    from tests.test_fujimoto_execution import seeded, linked_intent
    from maps.execution.paper_costs import preview_settlement, commit_settlement
    from maps.fujimoto.service import FujimotoService
    from maps.fujimoto.repository import FillEvent
    from maps.common.models import PaperCostSettlement, PaperCostEvidence
    settings, baseline, current, _, pending=incident
    repo,cycle,order=seeded(db,settings)
    intent,log=linked_intent(db,cycle,order)
    repo.bind_intent(order.id,intent.id,"broker")
    repo.apply_fill(FillEvent(order.id,cycle.account_key,intent.id,2,2000,0,0,"FILLED",current.ref_date))
    intent.status="FILLED";log.status="filled";log.fill_qty=2
    current.cash-=2000
    current.evidence={**current.evidence,"positions":{"AAA":{"qty":2}},
        "orders":{**current.evidence["orders"],"broker":{"ticker":"AAA","side":"buy","qty":2,"notional":"2000"}}}
    db.commit()
    request=statement(incident)
    request["items"].append({**request["items"][0],"scope":"order","broker_order_id":"broker",
        "amount":"20","gross":"2000","fees":"20","tax":"0"})
    preview=preview_settlement(db,settings,request)
    request.update(fingerprint=preview["fingerprint"],version=preview["version"],idempotency_key="rollback")
    original=FujimotoService.settle_costs
    def fail_after_ledger(self,*args,**kwargs):
        original(self,*args,**kwargs)
        raise ExecutionBlockedError("injected_after_order_ledger")
    monkeypatch.setattr(FujimotoService,"settle_costs",fail_after_ledger)
    with pytest.raises(ExecutionBlockedError,match="injected_after_order_ledger"):
        commit_settlement(db,settings,request,"admin",owner_user_id=7)
    assert db.query(PaperCostSettlement).count()==db.query(PaperCostEvidence).count()==0
    assert order.fees==0 and cycle.cost_basis==2000 and pending.status=="unclassified"
    monkeypatch.setattr(FujimotoService,"settle_costs",original)
    commit_settlement(db,settings,request,"admin",owner_user_id=7)
    assert order.fees==20 and cycle.cost_basis==2020 and pending.status=="costs_resolved"


@pytest.mark.parametrize("start,end,hour,stable,expected", [
    ("2026-10-12","2026-10-12",9,False,"10"),
    ("2026-10-12","2026-10-13",18,True,"110"),
    ("2026-10-09","2026-10-12",18,True,"110"),
    ("2026-10-12","2026-10-14",18,True,None),
    ("2026-10-12","2026-10-13",17,True,None),
    ("2026-10-12","2026-10-13",18,False,None),
])
def test_dated_cost_coverage_never_fills_missing_sessions(incident,start,end,hour,stable,expected):
    from maps.execution.paper_costs import _cost_delta
    settings,baseline,current,_,_=incident
    baseline.ref_date=dt.date.fromisoformat(start);current.ref_date=dt.date.fromisoformat(end)
    baseline.observed_at=dt.datetime.combine(baseline.ref_date,dt.time(hour-9))
    baseline.evidence={**baseline.evidence,"paper_costs":{"stable":stable},
        "cost_observation":{"ref_date":start,"valid":True,"today_total":"100"}}
    current.evidence={**current.evidence,
        "cost_observation":{"ref_date":end,"valid":True,"today_total":"110","previous_total":"999999"}}
    if expected is None:
        with pytest.raises(ExecutionBlockedError,match="coverage_missing"):
            _cost_delta(baseline,current,settings)
    else:
        assert _cost_delta(baseline,current,settings)==Decimal(expected)


def test_observe_does_not_advance_baseline_across_contradictory_costs(db,incident,monkeypatch):
    from maps.execution.reconciliation import reconcile
    from maps.execution.broker_adapter import AccountActivity,AccountBalance,PositionSnapshot
    settings,baseline,current,fee,pending=incident
    db.delete(current);db.delete(fee);db.delete(pending)
    baseline.ref_date=(utcnow()+dt.timedelta(hours=9)).date()
    baseline.cash=baseline.nav=10000
    baseline.evidence={"positions":{},"orders":{},"costs":"0","unsupported":[],
        "cost_observation":{"ref_date":str(baseline.ref_date),"valid":True,"today_total":"0"}}
    db.commit()
    observation={"ref_date":str(baseline.ref_date),"valid":True,"today_total":"100"}
    broker=SimpleNamespace(get_order_history=lambda *a:[],get_open_orders=lambda:[],
        get_execution_snapshot=lambda:PositionSnapshot({},AccountBalance(10000,0,10000),dt.datetime.now(dt.timezone.utc),cost_observation=observation),
        get_account_activity=lambda *a:AccountActivity(True,Decimal(0)))
    for _ in range(3):
        summary,_,_=reconcile(db,broker,settings)
        assert not summary["complete"]
        assert db.query(AccountObservation).filter_by(complete=True).one().id==baseline.id
    assert "paper_costs_cash_mismatch" in summary["block_reasons"]
