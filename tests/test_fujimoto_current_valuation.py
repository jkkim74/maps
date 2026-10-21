"""Current BUY permissions recheck EPS dependencies without rewriting frozen research."""
import base64
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
from unittest.mock import Mock
import zlib

import pytest

from maps.common.exceptions import ExecutionBlockedError
from maps.common.models import DartFilingReceipt, FujimotoCycle, FujimotoEvidence
from maps.fujimoto.repository import fingerprint, json_data
from maps.fujimoto.replay import screening_evidence
from tests.test_fujimoto_paper_trial import ready, NOW, trial_buy_source
from tests.test_fujimoto_valuation import historical_negative_proof, eps_row, identity_and_filing


def freeze_candidate(service,candidate,raw,*,ticker=None):
    """Append a real immutable candidate with its shared rebuilt rule."""
    ticker = ticker or candidate.ticker
    rule = screening_evidence(raw,ticker,date(2026,10,7))
    return service.repo.record_evidence("candidate",ticker,NOW,NOW,{**candidate.payload,"rule":rule,
        "raw_zlib":base64.b64encode(zlib.compress(json.dumps(json_data(raw)).encode())).decode()})


@pytest.fixture
def ready_v3(ready,db,monkeypatch):
    """A selected ordinary candidate genuinely depends on a reviewed negative-EPS peer."""
    service,key,candidate,_ = ready
    proof = historical_negative_proof(db,monkeypatch)
    raw = json.loads(zlib.decompress(base64.b64decode(candidate.payload["raw_zlib"])))
    raw["screening_version"] = 3
    raw["valuations"][0]["provider"] = "krx"
    raw["valuations"].append(dict(ticker="B",ref_date="2026-10-07",available_at="2026-10-07T13:00:00",
        per=None,status="unknown",strategy_exclusion=proof))
    raw["sectors"]["memberships"].append(["B","s"])
    raw["security_classifications"] = {}
    for ticker in (candidate.ticker,"B"):
        basic = dict(ISU_SRT_CD=ticker,KIND_STKCERT_TP_NM="보통주",SECT_TP_NM="",MKT_TP_NM="KOSPI",SECUGRP_NM="주권")
        raw["security_classifications"][ticker] = dict(ticker=ticker,ref_date="2026-10-07",observed_at="2026-10-07T13:00:00+00:00",
            raw=basic,raw_hash=fingerprint(basic),source_url="https://data.krx.co.kr/contents/MDC/MDI/mdiLoader")
    raw["prices"][-1]["close"] = 990
    candidate = freeze_candidate(service,candidate,raw)
    db.commit()
    assert service._paper_readiness(key,NOW,ticker=candidate.ticker) == []
    return service,key,candidate,raw


def supersede(db,monkeypatch,kind):
    """A currently known mutation is immediate even when financial availability is later."""
    import maps.fujimoto.valuation as module
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):
            return NOW
    with monkeypatch.context() as scoped:
        scoped.setattr(module,"datetime",Clock)
        if kind == "identity":
            module._record_identity_observation(db,"B",{"B":"changed"})
        elif kind == "source":
            module.record_eps_source(db,"B",corp_code="001",receipt="20260320000001",year=2025,basis="CFS",
                publication_date=date(2026,3,20),response={"status":"000","list":[{**eps_row(),"thstrm_amount":"-11"}]},
                **identity_and_filing())
        elif kind == "receipt":
            db.add(DartFilingReceipt(ticker="B",receipt="20261008000001",period_end=date(2025,12,31),
                publication_date=NOW.date(),first_collected_at=NOW,available_date=date(2026,10,12)))
        else:
            from sqlalchemy import delete
            db.execute(delete(FujimotoEvidence).where(FujimotoEvidence.kind == "eps_identity",FujimotoEvidence.ticker == "B"))
    db.commit()


def authorize_policy(service,key,monkeypatch,policy):
    """Keep unrelated measured approvals isolated from the current dependency check."""
    if policy == "paper_test":
        control = service.activate_paper_test(key,7,sell_consent=True)
        return service._control(key,{**control,"starts_at":NOW.isoformat()})
    gate = dict(config_ids={m:c.id for m,c in service.current_configs(key).items()},fingerprint="approved")
    monkeypatch.setattr("maps.fujimoto.service.FujimotoService.validation_gate",lambda *a,**kw:gate)
    return service._control(key,{**gate,"authorization_policy":"validated","execution_mode":"paper",
        "entries_enabled":True,"sell_consent":True,"replay_id":1})


@pytest.mark.parametrize("mutation",["identity","source","receipt","identity_missing"])
def test_current_readiness_rejects_superseded_peer_but_frozen_rebuild_is_unchanged(ready_v3,db,monkeypatch,mutation):
    service,key,candidate,raw = ready_v3
    frozen = deepcopy(raw)
    before = screening_evidence(frozen,candidate.ticker,date(2026,10,7))
    supersede(db,monkeypatch,mutation)
    assert "usable_screen_candidate_source_missing" in service._paper_readiness(key,NOW,ticker=candidate.ticker)
    assert raw == frozen and screening_evidence(frozen,candidate.ticker,date(2026,10,7)) == before


@pytest.mark.parametrize("policy",["paper_test","validated"])
@pytest.mark.parametrize("mutation",["identity","source","receipt","identity_missing"])
def test_both_buy_policies_recheck_peer_dependencies_and_preserve_sell_permission(ready_v3,db,monkeypatch,policy,mutation):
    service,key,candidate,_ = ready_v3
    control = authorize_policy(service,key,monkeypatch,policy)
    assert service.authorize_order(key,buy=True,now=NOW,ticker=candidate.ticker)["control_id"] == control["control_id"]
    supersede(db,monkeypatch,mutation)
    with pytest.raises(ExecutionBlockedError,match="superseded_strategy_exclusion"):
        service.authorize_order(key,buy=True,now=NOW,ticker=candidate.ticker)
    assert service.authorize_order(key,buy=False,now=NOW,ticker=candidate.ticker)["control_id"] == control["control_id"]


@pytest.mark.parametrize("policy",["paper_test","validated"])
@pytest.mark.parametrize("mutation",["identity","source","receipt"])
def test_final_source_guard_rechecks_already_reserved_buy(ready_v3,db,monkeypatch,policy,mutation):
    from maps.fujimoto.service import validate_source
    service,key,candidate,_ = ready_v3
    service,key,source,reservation,order,context = trial_buy_source((service,key,candidate,None))
    if policy == "validated":
        authorize_policy(service,key,monkeypatch,policy)
    assert validate_source(db,order,context,service.settings)
    supersede(db,monkeypatch,mutation)
    with pytest.raises(ExecutionBlockedError,match="superseded_strategy_exclusion"):
        validate_source(db,order,context,service.settings)
    assert reservation.status == "RESERVED"  # Permission checks do not fabricate order outcomes.


@pytest.mark.parametrize("policy",["paper_test","validated"])
def test_final_guard_checks_reserved_candidate_even_after_a_new_candidate_is_built(ready_v3,db,monkeypatch,policy):
    from maps.fujimoto.service import validate_source
    service,key,candidate,raw = ready_v3
    service,key,_,_,order,context = trial_buy_source((service,key,candidate,None))
    if policy == "validated":
        authorize_policy(service,key,monkeypatch,policy)
    supersede(db,monkeypatch,"identity")
    latest = deepcopy(raw)
    latest["valuations"] = latest["valuations"][:1]
    latest["sectors"]["memberships"] = latest["sectors"]["memberships"][:1]
    latest["security_classifications"].pop("B")
    freeze_candidate(service,candidate,latest)
    db.commit()
    assert service.authorize_order(key,buy=True,now=NOW,ticker=candidate.ticker)
    with pytest.raises(ExecutionBlockedError,match="superseded_strategy_exclusion"):
        validate_source(db,order,context,service.settings)


def test_stale_quoted_candidate_reserves_no_empty_cycles(ready_v3,db,monkeypatch):
    from maps.limit_up.feed import FeedQuote
    service,key,candidate,_ = ready_v3
    authorize_policy(service,key,monkeypatch,"paper_test")
    supersede(db,monkeypatch,"receipt")
    monkeypatch.setattr("maps.fujimoto.sources.current_financial_status",lambda *a:"maintained")
    monkeypatch.setattr(service.feed,"on_quote",lambda *a,**kw:(990,False))
    submit = Mock()
    monkeypatch.setattr(service,"_submit_decision",submit)
    service.on_quote(FeedQuote(candidate.ticker,991,100,990,300,1.,100,300,NOW,NOW),now=NOW,recorded=True)
    assert db.query(FujimotoCycle).count() == 0
    submit.assert_not_called()


def test_superseded_peer_does_not_block_owned_position_protective_exit(ready_v3,db,monkeypatch):
    from maps.fujimoto.domain import Mode, CycleState, evaluate
    from maps.fujimoto.repository import FillEvent
    from maps.limit_up.feed import FeedQuote
    service,key,candidate,raw = ready_v3
    authorize_policy(service,key,monkeypatch,"paper_test")
    cycle = service.repo.create_cycle(service.current_configs(key)["safe"].id,candidate.ticker)
    rule = screening_evidence(raw,candidate.ticker,date(2026,10,7))
    order = service.repo.reserve_order(cycle.id,evaluate(Mode.SAFE,rule,CycleState()),candidate.id,2,990,
        signal_date=rule.as_of,stop_price=920)
    service.repo.apply_fill(FillEvent(order.id,key,None,2,1980,0,0,"FILLED",date(2026,10,7)))
    db.commit()
    supersede(db,monkeypatch,"source")
    monkeypatch.setattr("maps.fujimoto.sources.current_financial_status",lambda *a:"maintained")
    monkeypatch.setattr(service.feed,"on_quote",lambda *a,**kw:(900,False))
    submit = Mock()
    monkeypatch.setattr(service,"_submit_decision",submit)
    service.on_quote(FeedQuote(candidate.ticker,901,100,900,300,1.,100,300,NOW,NOW),now=NOW,recorded=True)
    assert submit.call_count == 1
    assert submit.call_args.args[2].action == "sell" and submit.call_args.args[2].reason == "price_stop"


def test_quote_admission_skips_stale_dependencies_before_the_five_candidate_limit(ready_v3,db,monkeypatch):
    from maps.limit_up.feed import FeedQuote
    service,key,candidate,raw = ready_v3
    authorize_policy(service,key,monkeypatch,"paper_test")
    supersede(db,monkeypatch,"identity")
    names = [f"STALE{i}" for i in range(5)]
    for name in names:
        other = deepcopy(raw)
        for row in other["annual_records"] + other["financial_records"]:
            row["ticker"] = name
        other["valuations"][0]["ticker"] = name
        other["sectors"]["memberships"][0][0] = name
        meta = other["security_classifications"].pop(candidate.ticker)
        meta["ticker"] = meta["raw"]["ISU_SRT_CD"] = name
        meta["raw_hash"] = fingerprint(meta["raw"])
        other["security_classifications"][name] = meta
        freeze_candidate(service,candidate,other,ticker=name)
    valid = deepcopy(raw)
    valid["valuations"] = valid["valuations"][:1]
    valid["sectors"]["memberships"] = valid["sectors"]["memberships"][:1]
    valid["security_classifications"].pop("B")
    freeze_candidate(service,candidate,valid)
    service.repo.record_evidence("screen","*",NOW,NOW,dict(ref_date="2026-10-07",ranked=names+[candidate.ticker]))
    db.commit()
    monkeypatch.setattr("maps.fujimoto.sources.current_financial_status",lambda *a:"maintained")
    monkeypatch.setattr(service.feed,"on_quote",lambda *a,**kw:(990,False))
    submit = Mock()
    monkeypatch.setattr(service,"_submit_decision",submit)
    service.on_quote(FeedQuote(candidate.ticker,991,100,990,300,1.,100,300,NOW,NOW),now=NOW,recorded=True)
    assert {r.ticker for r in db.query(FujimotoCycle)} == {candidate.ticker}
    assert submit.call_count == 2


def test_valuation_only_code_change_invalidates_replay_and_paper_binding(ready,monkeypatch):
    from maps.fujimoto.replay import code_fingerprint
    service,key,_,_ = ready
    control = service.activate_paper_test(key,7,sell_consent=True)
    before = code_fingerprint()
    replay = service.repo.record_evidence("replay","*",NOW,NOW,{"inputs":{}},account_key=key)
    binding = dict(replay_id=replay.id,code_hash=before,replay_fingerprint=replay.fingerprint,
        execution_params=dict(with_orderbook=True,account_mdd_limit=service.settings.maps_account_mdd_limit),
        runtime_parameters=dict(fee_rate=.00015,tax_rate=.002,slippage=.001,budget=10000000.,
            account_ticker_limit=service.settings.max_single_exposure,
            minimum_cash_fraction=max(.325,service.settings.maps_min_cash_ratio_weak,
                service.settings.maps_min_cash_ratio_mixed,service.settings.maps_min_cash_ratio_strong)))
    service.db.commit()
    read_text = Path.read_text
    def changed(path,*a,**kw):
        return read_text(path,*a,**kw) + ("\n# valuation-only change\n" if path.name == "valuation.py" else "")
    monkeypatch.setattr(Path,"read_text",changed)
    assert code_fingerprint() != before
    assert service._trial_binding(key,control)
    with pytest.raises(ExecutionBlockedError,match="binding_changed"):
        service.authorize_order(key,buy=True,now=NOW,ticker="005930")
    with pytest.raises(ExecutionBlockedError,match="validation_data_or_code_changed"):
        service.validation_gate(key,replay.id,binding=binding)


@pytest.mark.parametrize("policy",["paper_test","validated"])
def test_dependency_change_during_broker_snapshot_blocks_before_reservation(ready_v3,db,monkeypatch,policy):
    from types import SimpleNamespace
    from maps.fujimoto.domain import Mode, CycleState, evaluate
    from maps.common.models import FujimotoOrder
    service,key,candidate,raw = ready_v3
    authorize_policy(service,key,monkeypatch,policy)
    cycle = service.repo.create_cycle(service.current_configs(key)["original"].id,candidate.ticker)
    rule = screening_evidence(raw,candidate.ticker,date(2026,10,7))
    def broker_snapshot():
        supersede(db,monkeypatch,"receipt")
        return SimpleNamespace(as_of=datetime.now(timezone.utc),positions={},
            balance=SimpleNamespace(total_value=100000000,cash=100000000))
    service.manager._broker.get_execution_snapshot.side_effect = broker_snapshot
    service.manager._broker.get_open_orders.return_value = []
    monkeypatch.setattr(service.repo,"plan_buy",lambda *a,**kw:SimpleNamespace(quantity=2,limit_price=990,stop_price=None))
    with pytest.raises(ExecutionBlockedError,match="superseded_strategy_exclusion"):
        service._submit_decision(cycle,rule,evaluate(Mode.ORIGINAL,rule,CycleState()),990,candidate,NOW)
    assert db.query(FujimotoOrder).count() == 0
    service.manager.submit.assert_not_called()


@pytest.mark.parametrize("has_dependency",[True,False])
def test_current_validation_queries_only_candidate_dependencies(ready_v3,db,has_dependency):
    from sqlalchemy import event
    from maps.fujimoto.valuation import validate_current_exclusions
    service,key,candidate,raw = ready_v3
    if not has_dependency:
        raw["valuations"] = raw["valuations"][:1]
    statements = []
    def observed(conn,cursor,statement,parameters,context,executemany):
        statements.append((statement,parameters))
    event.listen(db.bind,"before_cursor_execute",observed)
    try:
        validate_current_exclusions(db,raw,date(2026,10,7),NOW)
    finally:
        event.remove(db.bind,"before_cursor_execute",observed)
    assert len(statements) == (2 if has_dependency else 0)
    assert all("ticker IN" in sql and "B" in parameters for sql,parameters in statements)
