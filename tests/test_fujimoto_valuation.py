"""Signed annual EPS and official membership must fail closed in screening v3."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone

import pytest

from maps.common.exceptions import DataQualityError


def eps_row(**changes):
    """A standard, explicitly common annual basic EPS statement row."""
    return dict(rcept_no="20260320000001", corp_code="001", stock_code="B",
        fs_div="CFS", bsns_year="2025", reprt_code="11011", sj_div="IS",
        account_id="ifrs-full_BasicEarningsLossPerShare", account_nm="기본주당이익(손실)",
        account_detail="보통주", thstrm_nm="제 20 기", thstrm_dt="2025.01.01 ~ 2025.12.31",
        currency="KRW", thstrm_amount="-10", **changes)


def identity_and_filing():
    """Official corpCode mapping and list.json receipt archive."""
    return dict(identity={"B":"001"}, filing={"corp_code":"001","stock_code":"B",
        "rcept_no":"20260320000001","rcept_dt":"20260320","report_nm":"사업보고서 (2025.12)"})


def test_signed_basic_common_eps_and_complete_components():
    from maps.fujimoto.valuation import common_eps
    row = eps_row()
    assert common_eps([row], "B", "001", row["rcept_no"], "CFS", 2025) == -10
    components = [{**row, "account_id": tag, "thstrm_amount": value} for tag, value in (
        ("ifrs-full_BasicEarningsLossPerShareFromContinuingOperations", "-20"),
        ("ifrs-full_BasicEarningsLossPerShareFromDiscontinuedOperations", "5"))]
    assert common_eps(components, "B", "001", row["rcept_no"], "CFS", 2025) == -15
    for rows in (components[:1], [row, *components], [{**row, "account_detail": "보통주 및 우선주"}],
                 [{**row, "account_id": "entity_customEPS"}],
                 [{**row, "account_id": "ifrs-full_ProfitLossAttributableToOwnersOfParent"}],
                 [components[0], {**components[1], "thstrm_nm": "other"}]):
        with pytest.raises(DataQualityError):
            common_eps(rows, "B", "001", row["rcept_no"], "CFS", 2025)


@pytest.mark.parametrize("field,value", [("rcept_no", "new"), ("fs_div", "OFS"),
    ("bsns_year", "2024"), ("reprt_code", "11012"), ("stock_code", "C"),
    ("corp_code", "002"), ("thstrm_dt", "2024.01.01 ~ 2024.12.31")])
def test_eps_exact_context(field, value):
    from maps.fujimoto.valuation import common_eps
    row = eps_row()
    with pytest.raises(DataQualityError):
        common_eps([{**row, field: value}], "B", "001", row["rcept_no"], "CFS", 2025)


def test_eps_source_review_is_causal_receipt_bound_and_superseded(db):
    from maps.fujimoto.valuation import record_eps_source, review_eps_source, exclusion_inputs, validate_exclusion
    from maps.common.models import DartFilingReceipt
    from maps.data.dart_financials import KST
    from maps.fujimoto.repository import utc_naive
    now = datetime.now(timezone.utc)
    today = now.astimezone(KST).date()
    row = eps_row()
    source = record_eps_source(db, "B", corp_code="001", receipt=row["rcept_no"], year=2025,
        basis="CFS", publication_date=date(2026, 3, 20), response={"status": "000", "list": [row]}, **identity_and_filing())
    review = review_eps_source(db, source.id, owner=1, explanation="Exact annual common EPS statement and corporation reviewed.")
    assert review.available_at > utc_naive(now)
    assert exclusion_inputs(db, today, datetime.now(timezone.utc)) == {}
    future = review.available_at.replace(tzinfo=timezone.utc) + timedelta(hours=12)
    proofs = exclusion_inputs(db, future.astimezone(KST).date(), future)
    proof = proofs["B"]
    validate_exclusion(proof, "B", future.astimezone(KST).date(), future)
    for part, field, value in (("source", "receipt", "new"), ("source", "basis", "OFS"),
                               ("review", "source_hash", "a" * 64)):
        bad = deepcopy(proof)
        bad[part][field] = value
        with pytest.raises(DataQualityError):
            validate_exclusion(bad, "B", future.astimezone(KST).date(), future)
    db.add(DartFilingReceipt(ticker="B", receipt="20261022000001", period_end=date(2025,12,31),
        publication_date=today, first_collected_at=now, available_date=today + timedelta(days=1)))
    db.commit()
    assert exclusion_inputs(db, future.astimezone(KST).date(), future) == {}


@pytest.mark.parametrize("amount", ["0", "10"])
def test_nonnegative_eps_cannot_be_reviewed_for_exclusion(db, amount):
    from maps.fujimoto.valuation import record_eps_source, review_eps_source
    row = {**eps_row(), "thstrm_amount": amount}
    source = record_eps_source(db, "B", corp_code="001", receipt=row["rcept_no"], year=2025,
        basis="CFS", publication_date=date(2026,3,20), response={"status":"000", "list":[row]}, **identity_and_filing())
    with pytest.raises(DataQualityError):
        review_eps_source(db, source.id, owner=1, explanation="Annual common EPS source checked against filing identity.")


def test_official_metadata_is_exact_date_no_name_heuristic(db):
    from maps.fujimoto.valuation import record_security_source, security_inputs, security_kind
    from maps.data.dart_financials import KST
    now = datetime.now(timezone.utc)
    day = now.astimezone(KST).date()
    def raw(ticker, kind, sector):
        return {"ISU_SRT_CD": ticker, "KIND_STKCERT_TP_NM": kind, "SECT_TP_NM": sector,"SECUGRP_NM":"주권","MKT_TP_NM":"KOSPI",
                "ISU_ABBRV": "Misleading우스팩"}
    for ticker, kind, sector in (("A", "보통주", "일반기업부"), ("P", "구형우선주", "일반기업부"),
                                  ("S", "보통주", "SPAC(소속부없음)"), ("U", "", "")):
        record_security_source(db, day, raw(ticker, kind, sector))
    proof = security_inputs(db, day, datetime.now(timezone.utc))
    assert [security_kind(proof.get(t), t, day) for t in ("A","P","S","U","missing")] == [
        "ordinary", "excluded", "excluded", "unknown", "unknown"]
    assert security_inputs(db, day - timedelta(days=1), now) == {}
    with pytest.raises(DataQualityError):
        record_security_source(db, day - timedelta(days=1), raw("X", "보통주", "일반기업부"))


def test_v3_requires_metadata_and_keeps_ineligible_ordinary_peers(db):
    from maps.fujimoto.evidence import screen, SectorSnapshot, ValuationRecord
    from tests.test_fujimoto_evidence import bars
    from maps.fujimoto.valuation import record_security_source, security_inputs
    from maps.data.dart_financials import KST
    now = datetime.now(timezone.utc)
    day = now.astimezone(KST).date()
    sectors = SectorSnapshot(day, now, (("A","s"),("B","s"),("P","s")))
    vals = [ValuationRecord("A", day, now, 10,provider="krx"), ValuationRecord("B",day,now,None)]
    for t, kind in (("A","보통주"),("B","보통주"),("P","구형우선주")):
        record_security_source(db,day, {"ISU_SRT_CD":t,"KIND_STKCERT_TP_NM":kind,"SECT_TP_NM":"일반기업부","SECUGRP_NM":"주권","MKT_TP_NM":"KOSPI"})
    proof = security_inputs(db,day,datetime.now(timezone.utc))
    result = screen("A",[],vals,sectors,bars(),day,eligible=True,screening_version=3,security_classifications=proof)
    assert "incomplete_sector_valuation" in result.reasons
    vals[1] = ValuationRecord("B",day,now,20,provider="krx")
    result = screen("A",[],vals,sectors,bars(),day,eligible=True,screening_version=3,security_classifications=proof)
    assert "incomplete_sector_valuation" not in result.reasons and result.sector_median_per == 15
    assert "missing_security_classification" in screen("A",[],vals,sectors,bars(),day,eligible=True,screening_version=3).reasons
    assert "excluded_security" in screen("P",[],vals,sectors,bars(),day,eligible=True,screening_version=3,security_classifications=proof).reasons


def test_diagnostic_sidecar_cannot_replace_positive_krx_per(db):
    from maps.fujimoto.sources import record_valuation_source, valuation_inputs
    from maps.data.dart_financials import KST
    now = datetime.now(timezone.utc)
    day = now.astimezone(KST).date()
    record_valuation_source(db,"A",day,per=None,raw={"PER":"-"},
        source_url="https://openapi.koreainvestment.com/value",document_hash="a"*64)
    value = valuation_inputs(db,day,datetime.now(timezone.utc),[dict(ticker="A",ref_date=day,available_at=now,per=10)])[0]
    assert value["per"] == 10


def test_eps_collection_includes_ineligible_peers_and_never_switches_after_cfs_error(db):
    from maps.fujimoto.valuation import EPSCollector
    from maps.fujimoto.repository import FujimotoRepository
    from maps.data.dart_financials import KST
    now = datetime.now(timezone.utc)
    day = now.astimezone(KST).date()
    FujimotoRepository(db).record_evidence("universe","*",now,now,{"ref_date":day,
        "members":[{"ticker":"B","eligible":False}]})
    db.commit()
    class Client:
        requests = 0
        bases = []
        def corp_codes(self):
            return {"B":"001"}
        def request(self,endpoint,**params):
            self.requests += 1
            if endpoint == "list.json":
                return {"status":"000","list":[identity_and_filing()["filing"]]}
            self.bases.append(params["fs_div"])
            raise ValueError("transient_exhausted")
    client = Client()
    report = EPSCollector(db,"key",client=client).collect(day,batch=1,tickers=["B"])
    assert report["attempted"] == ["B"] and client.bases == ["CFS"]
    assert report["errors"]["B"] == "transient_exhausted"


def test_normal_screen_v3_freezes_metadata_and_same_data_comparison(db):
    from tests.test_fujimoto_sources import _screen_universe, _candidate_raw
    from maps.fujimoto.sources import screen_market
    from maps.common.models import FujimotoEvidence
    from maps.fujimoto.repository import FujimotoRepository, fingerprint, json_data
    day, now = _screen_universe(db,["005930"])
    raw = dict(ISU_SRT_CD="005930",KIND_STKCERT_TP_NM="보통주",SECT_TP_NM="",SECUGRP_NM="주권",MKT_TP_NM="KOSPI")
    report = screen_market(db,day,now=now)
    candidate = db.query(FujimotoEvidence).filter_by(kind="candidate").one()
    assert _candidate_raw(candidate)["screening_version"] == 3
    assert "005930" in _candidate_raw(candidate)["security_classifications"]
    assert report["valuation_states"] == {"numeric":1}
    assert report["comparison"]["v2_selected"] == report["comparison"]["v3_selected"] == 1


def test_eps_proof_rejects_identity_archive_and_receipt_context_tampering(db):
    from maps.fujimoto.valuation import record_eps_source, review_eps_source
    for changed in (dict(identity={"B":"other"}),dict(filing={**identity_and_filing()["filing"],"rcept_dt":"20260319"})):
        source = record_eps_source(db,"B",corp_code="001",receipt="20260320000001",year=2025,basis="CFS",
            publication_date=date(2026,3,20),response={"status":"000","list":[eps_row()]},
            **{**identity_and_filing(),**changed})
        with pytest.raises(DataQualityError):
            review_eps_source(db,source.id,owner=1,explanation="Exact annual common EPS statement and corporation reviewed.")


def test_valuation_cli_report_is_read_only_and_review_requires_apply(db, monkeypatch, tmp_path):
    import json
    from scripts.fujimoto_valuation import main
    from maps.common.models import FujimotoEvidence
    output = tmp_path / "report.json"
    monkeypatch.setattr("sqlalchemy.create_engine",lambda _:db.bind)
    monkeypatch.setattr("sys.argv",["fujimoto_valuation.py","report","--database","sqlite://",
        "--output",str(output)])
    main()
    assert json.loads(output.read_text())["execution_permission"] is False
    assert db.query(FujimotoEvidence).count() == 0


def test_explicit_common_loss_is_separate_from_preferred_share_eps():
    from maps.fujimoto.valuation import common_eps
    common = {**eps_row(),"account_nm":"보통주기본주당이익(손실)","account_detail":"-","thstrm_amount":"-123"}
    preferred = {**common,"account_id":"entity_PreferredBasicEPS","account_nm":"우선주기본주당순이익(손실)","thstrm_amount":"5"}
    assert common_eps([common,preferred],"B","001",common["rcept_no"],"CFS",2025) == -123


def historical_negative_proof(db, monkeypatch):
    """Observe/review on the test clock, then replay after actual next-session availability."""
    import maps.fujimoto.valuation as module
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):
            return datetime(2026,7,1,3,tzinfo=timezone.utc)
    with monkeypatch.context() as scoped:
        scoped.setattr(module,"datetime",Clock)
        source = module.record_eps_source(db,"B",corp_code="001",receipt="20260320000001",year=2025,
            basis="CFS",publication_date=date(2026,3,20),response={"status":"000","list":[eps_row()]},**identity_and_filing())
        module.review_eps_source(db,source.id,owner=1,explanation="Exact annual common EPS statement and corporation reviewed.")
    return module.exclusion_inputs(db,date(2026,10,21),datetime.now(timezone.utc))["B"]


def test_negative_peer_is_excluded_only_in_v3_and_cannot_enter_own_candidate(db, monkeypatch):
    from maps.fujimoto.evidence import screen, SectorSnapshot, ValuationRecord
    from maps.fujimoto.valuation import record_security_source,security_inputs
    from tests.test_fujimoto_evidence import bars
    proof = historical_negative_proof(db,monkeypatch)
    now = datetime.now(timezone.utc)
    from maps.data.dart_financials import KST
    day = now.astimezone(KST).date()
    for t in ("A","B"):
        record_security_source(db,day,dict(ISU_SRT_CD=t,KIND_STKCERT_TP_NM="보통주",SECT_TP_NM="",SECUGRP_NM="주권",MKT_TP_NM="KOSPI"))
    metadata = security_inputs(db,day,datetime.now(timezone.utc))
    vals = [ValuationRecord("A",day,now,10,provider="krx"),ValuationRecord("B",day,now,None,strategy_exclusion=proof)]
    sector = SectorSnapshot(day,now,(("A","s"),("B","s")))
    assert "incomplete_sector_valuation" in screen("A",[],vals,sector,bars(),day,eligible=True,screening_version=2).reasons
    passed = screen("A",[],vals,sector,bars(),day,eligible=True,screening_version=3,security_classifications=metadata)
    assert "incomplete_sector_valuation" not in passed.reasons and passed.sector_median_per == 10
    assert "negative_common_eps" in screen("B",[],vals,sector,bars(),day,eligible=True,screening_version=3,security_classifications=metadata).reasons
    # An unresolved newer observation masks an older reviewed negative value immediately.
    from maps.fujimoto.valuation import record_eps_source,exclusion_inputs
    record_eps_source(db,"B",corp_code="001",receipt="20260320000001",year=2025,basis="CFS",
        publication_date=date(2026,3,20),response={"status":"013"},**identity_and_filing())
    assert exclusion_inputs(db,day,datetime.now(timezone.utc)) == {}


def test_future_and_stale_eps_proofs_reject_on_shared_replay(db, monkeypatch):
    from maps.fujimoto.valuation import validate_exclusion
    from maps.fujimoto.repository import fingerprint
    proof = historical_negative_proof(db,monkeypatch)
    for field,value in (("year",2024),("observed_at",(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()),("ticker","OTHER")):
        bad = deepcopy(proof)
        bad["source"][field] = value
        bad["review"]["source_hash"] = fingerprint(bad["source"])
        with pytest.raises(DataQualityError):
            validate_exclusion(bad,"B",date(2026,10,21),datetime.now(timezone.utc))


def test_cfs_no_data_alone_does_not_authorize_ofs_exclusion(db):
    from maps.fujimoto.valuation import record_eps_source, review_eps_source
    source = record_eps_source(db,"B",corp_code="001",receipt="20260320000001",year=2025,basis="OFS",
        publication_date=date(2026,3,20),response={"status":"000","list":[{**eps_row(),"fs_div":"OFS"}]},
        cfs_nonapplicability={"status":"013"},**identity_and_filing())
    with pytest.raises(DataQualityError,match="explicit_cfs_nonapplicability_required"):
        review_eps_source(db,source.id,owner=1,explanation="An unavailable consolidated response does not establish absence.")


def test_daily_job_refreshes_bounded_eps_with_shared_dart_request_budget(db,monkeypatch):
    from maps.ops.scheduler import OperationalPipeline
    from maps.common.settings import MapsSettings
    from maps.fujimoto.sources import AnnualCollector
    from maps.fujimoto.valuation import EPSCollector
    import maps.fujimoto.valuation as module
    from maps.data.dart_financials import DartClient
    seen = []
    monkeypatch.setattr(module,"collect_metadata",lambda *a:seen.append("metadata") or {"status":"success"})
    monkeypatch.setattr(EPSCollector,"collect",lambda self,*a,**kw:seen.append(("eps",self.client,kw["batch"])) or {})
    monkeypatch.setattr(AnnualCollector,"collect",lambda self,*a,**kw:seen.append(("annual",self.client,kw["batch"])) or {})
    monkeypatch.setattr("maps.fujimoto.sources.screen_market",lambda *a:seen.append("screen") or {})
    pipeline = OperationalPipeline(settings=MapsSettings(_env_file=None,maps_fujimoto_enabled=True,
        maps_broker_mode="mock",maps_fujimoto_collect_batch=2),session_factory=lambda:db)
    monkeypatch.setattr(pipeline,"_job",lambda name,fn:fn(db))
    result = pipeline.run_fujimoto_screen(date(2026,10,21))
    assert [r if isinstance(r,str) else r[0] for r in seen] == ["metadata","eps","annual","screen"]
    assert isinstance(seen[1][1],DartClient) and seen[1][1] is seen[2][1]
    assert seen[1][2] == seen[2][2] == 2
    assert "valuation_collection" in result


def test_existing_fss_provider_contract_review_survives_separate_dart_strategy_flow(db):
    from maps.fujimoto.sources import record_valuation_source, record_valuation_review, valuation_inputs
    from maps.data.dart_financials import KST
    now = datetime.now(timezone.utc)
    day = now.astimezone(KST).date()
    source = record_valuation_source(db,"B",day,per=None,raw={"PER":"-"},
        source_url="https://dart.fss.or.kr/source",document_hash="a"*64)
    record_valuation_review(db,"B",day,source_id=source.id,contract_url="https://dart.fss.or.kr/contract",
        contract_hash="b"*64,explanation="Existing exact-provider non-applicability contract reviewed.",owner=1)
    assert valuation_inputs(db,day,datetime.now(timezone.utc),[])[0]["status"] == "verified_not_applicable"
    kept = valuation_inputs(db,day,datetime.now(timezone.utc),[dict(ticker="B",ref_date=day,available_at=now,per=10)])
    assert kept[0]["per"] == 10


def test_newer_naver_shared_value_cannot_override_krx_sidecar(db):
    from maps.fujimoto.sources import record_valuation_source, valuation_inputs
    from maps.data.dart_financials import KST
    now = datetime.now(timezone.utc)
    day = now.astimezone(KST).date()
    record_valuation_source(db,"A",day,per=10,raw={"PER":"10"},
        source_url="https://data.krx.co.kr/value",document_hash="a"*64)
    values = valuation_inputs(db,day,datetime.now(timezone.utc),[dict(ticker="A",ref_date=day,
        available_at=datetime.now(timezone.utc),per=12,provider="naver")])
    assert values[0]["per"] == 10


def test_v3_frozen_diagnostic_numeric_per_never_becomes_authoritative():
    from maps.fujimoto.evidence import ValuationRecord, SectorSnapshot, screen
    from tests.test_fujimoto_evidence import bars
    now = datetime.now(timezone.utc)
    day = date(2026,10,21)
    value = ValuationRecord("A",day,now,10,provider="naver")
    result = screen("A",[],[value],SectorSnapshot(day,now,(("A","s"),)),bars(),day,eligible=True,screening_version=3)
    assert result.sector_median_per is None and "missing_valuation" in result.reasons


def test_identical_eps_refresh_retains_first_observation_and_review(db,monkeypatch):
    from maps.fujimoto.valuation import record_eps_source,exclusion_inputs
    proof = historical_negative_proof(db,monkeypatch)
    same = record_eps_source(db,"B",corp_code="001",receipt="20260320000001",year=2025,basis="CFS",
        publication_date=date(2026,3,20),response={"status":"000","list":[eps_row()]},**identity_and_filing())
    assert same.id == proof["source_id"]
    assert exclusion_inputs(db,date(2026,10,21),datetime.now(timezone.utc))["B"]["source_id"] == same.id
    company = {"status":"000","stock_code":"B","corp_code":"001","corp_name":"Official name"}
    same_company = record_eps_source(db,"B",corp_code="001",receipt="20260320000001",year=2025,basis="CFS",
        publication_date=date(2026,3,20),response={"status":"000","list":[eps_row()]},
        **{**identity_and_filing(),"identity":company})
    assert same_company.id == same.id
    changed = record_eps_source(db,"B",corp_code="changed",receipt="20260320000001",year=2025,basis="CFS",
        publication_date=date(2026,3,20),response={"status":"000","list":[eps_row()]},
        **{**identity_and_filing(),"identity":{"B":"changed"}})
    assert changed.id != same.id
    assert exclusion_inputs(db,date(2026,10,21),datetime.now(timezone.utc)) == {}


def test_metadata_refresh_reports_missing_exact_date_peers(db,monkeypatch):
    from maps.fujimoto.valuation import collect_metadata
    from maps.fujimoto.repository import FujimotoRepository
    from maps.data.dart_financials import KST
    import pandas as pd
    now = datetime.now(timezone.utc)
    day = now.astimezone(KST).date()
    FujimotoRepository(db).record_evidence("universe","*",now,now,{"ref_date":day,"members":[{"ticker":"MISSING","eligible":True}]})
    db.commit()
    monkeypatch.setattr("maps.data.krx_adapter.fetch_basic_information",lambda:pd.DataFrame([dict(
        ISU_SRT_CD="A",KIND_STKCERT_TP_NM="보통주",SECT_TP_NM="",SECUGRP_NM="주권",MKT_TP_NM="KOSPI")]))
    report = collect_metadata(db,day)
    assert report["status"] == "partial" and report["missing_tickers"] == ["MISSING"]


@pytest.mark.parametrize("field",["SECUGRP_NM","MKT_TP_NM"])
@pytest.mark.parametrize("value",["",None,"unrecognized"])
def test_unknown_official_group_or_market_keeps_missing_per_peer_blocking(db,field,value):
    from maps.fujimoto.evidence import screen, SectorSnapshot, ValuationRecord
    from maps.fujimoto.valuation import record_security_source, security_inputs, security_kind
    from maps.data.dart_financials import KST
    from tests.test_fujimoto_evidence import bars
    now = datetime.now(timezone.utc)
    day = now.astimezone(KST).date()
    for ticker in ("A","B"):
        raw = dict(ISU_SRT_CD=ticker,KIND_STKCERT_TP_NM="보통주",SECT_TP_NM="",
                   SECUGRP_NM="주권",MKT_TP_NM="KOSPI")
        if ticker == "B":
            raw[field] = value
        record_security_source(db,day,raw)
    metadata = security_inputs(db,day,datetime.now(timezone.utc))
    assert security_kind(metadata["B"],"B",day) == "unknown"
    values = [ValuationRecord("A",day,now,10,provider="krx"),ValuationRecord("B",day,now,None)]
    result = screen("A",[],values,SectorSnapshot(day,now,(("A","s"),("B","s"))),bars(),day,
        eligible=True,screening_version=3,security_classifications=metadata)
    assert "missing_security_classification" in result.reasons
    assert "incomplete_sector_valuation" in result.reasons


@pytest.mark.parametrize("failure",["list_transient","statement_transient","no_annual"])
def test_collector_identity_change_survives_later_failure_and_old_snapshot_replays(db,monkeypatch,failure):
    from maps.fujimoto.valuation import EPSCollector, exclusion_inputs, record_security_source, security_inputs, record_eps_source
    from maps.fujimoto.repository import FujimotoRepository, json_data
    from maps.fujimoto.replay import screening_evidence
    from maps.data.dart_financials import KST
    from maps.common.models import FujimotoEvidence
    proof = historical_negative_proof(db,monkeypatch)
    now = datetime.now(timezone.utc)
    day = now.astimezone(KST).date()
    repo = FujimotoRepository(db)
    repo.record_evidence("universe","*",now,now,{"ref_date":day,"members":[{"ticker":"B","eligible":False}]})
    for ticker in ("A","B"):
        record_security_source(db,day,dict(ISU_SRT_CD=ticker,KIND_STKCERT_TP_NM="보통주",SECT_TP_NM="",
            SECUGRP_NM="주권",MKT_TP_NM="KOSPI"))
    raw = json_data(dict(screening_version=3,annual_records=[],financial_records=[],eligible=True,
        security_classifications=security_inputs(db,day,datetime.now(timezone.utc)),
        sectors=dict(ref_date=day,available_at=now,memberships=[["A","s"],["B","s"]]),
        valuations=[dict(ticker="A",ref_date=day,available_at=now,per=10,provider="krx"),
                    dict(ticker="B",ref_date=day,available_at=now,per=None,strategy_exclusion=proof)],
        prices=[dict(date=day,open=10,high=11,low=9,close=10,volume=1)]))
    frozen = deepcopy(raw)
    before = screening_evidence(frozen,"A",day)
    assert "incomplete_sector_valuation" not in before.blocking_reasons
    class Client:
        requests = 0
        def corp_codes(self):
            return {"B":"changed"}
        def request(self,endpoint,**params):
            self.requests += 1
            if endpoint == "list.json" and failure == "no_annual":
                return {"status":"013"}
            if endpoint == "list.json" and failure == "statement_transient":
                return {"status":"000","list":[{**identity_and_filing()["filing"],"corp_code":"changed"}]}
            raise ValueError("transient_exhausted")
    result = EPSCollector(db,"key",client=Client()).collect(day,batch=1,tickers=["B"])
    assert result["errors"]["B"] == ("applicable_annual_filing_missing" if failure == "no_annual" else "transient_exhausted")
    assert exclusion_inputs(db,day,datetime.now(timezone.utc)) == {}
    identities = db.query(FujimotoEvidence).filter_by(kind="eps_identity",ticker="B").order_by(FujimotoEvidence.id).all()
    assert identities[-1].payload["corp_code"] == "changed"
    # Re-importing the exact archive can reuse the EPS source, never override a
    # newer independently observed identity or revive its old reviewed exclusion.
    imported = record_eps_source(db,"B",corp_code="001",receipt="20260320000001",year=2025,basis="CFS",
        publication_date=date(2026,3,20),response=proof["source"]["response"],**identity_and_filing())
    assert imported.id == proof["source_id"]
    assert exclusion_inputs(db,day,datetime.now(timezone.utc)) == {}
    assert db.query(FujimotoEvidence).filter_by(kind="eps_identity",ticker="B").order_by(
        FujimotoEvidence.id.desc()).first().id == identities[-1].id
    assert frozen == raw and screening_evidence(frozen,"A",day) == before


def test_noop_collection_does_not_invent_missing_identity(db,monkeypatch):
    from maps.fujimoto.valuation import EPSCollector, exclusion_inputs
    from maps.data.dart_financials import KST
    proof = historical_negative_proof(db,monkeypatch)
    day = datetime.now(timezone.utc).astimezone(KST).date()
    class Client:
        requests = 0
        def corp_codes(self):
            raise AssertionError("No targets must not request or invent an identity observation")
    result = EPSCollector(db,"key",client=Client()).collect(day,tickers=[])
    assert result["attempted"] == []
    assert exclusion_inputs(db,day,datetime.now(timezone.utc))["B"]["identity_observation"] == proof["identity_observation"]


@pytest.mark.parametrize("field,value",[("corp_code","other"),("ticker","other"),
    ("identity_hash","a"*64),("observed_at","2099-01-01T00:00:00+00:00")])
def test_frozen_identity_context_is_validated_on_shared_rebuild(db,monkeypatch,field,value):
    from maps.fujimoto.valuation import validate_exclusion
    proof = historical_negative_proof(db,monkeypatch)
    proof["identity_observation"]["source"][field] = value
    with pytest.raises(DataQualityError,match="invalid_strategy_exclusion"):
        validate_exclusion(proof,"B",date(2026,10,21),datetime.now(timezone.utc))


def test_changed_archived_financials_cannot_replace_newer_independent_identity(db,monkeypatch):
    import maps.fujimoto.valuation as module
    from maps.common.models import FujimotoEvidence
    proof = historical_negative_proof(db,monkeypatch)
    frozen = deepcopy(proof)
    class Clock(datetime):
        instant = datetime(2026,10,1,3,tzinfo=timezone.utc)
        @classmethod
        def now(cls,tz=None):
            return cls.instant
    with monkeypatch.context() as scoped:
        scoped.setattr(module,"datetime",Clock)
        fresh = module._record_identity_observation(db,"B",{"B":"changed"})
        assert module.exclusion_inputs(db,date(2026,10,1),Clock.instant) == {}
        Clock.instant = datetime(2026,10,2,3,tzinfo=timezone.utc)
        source = module.record_eps_source(db,"B",corp_code="001",receipt="20260320000001",year=2025,basis="CFS",
            publication_date=date(2026,3,20),response={"status":"000","list":[{**eps_row(),"thstrm_amount":"-11"}]},
            **identity_and_filing())
        assert source.id != proof["source_id"]
        # Even a new review of changed archived data, past its next-session
        # boundary, cannot contradict the independently known corporation.
        module.review_eps_source(db,source.id,owner=1,explanation="Reviewed changed archived annual common EPS statement.")
    assert module.exclusion_inputs(db,date(2026,10,21),datetime.now(timezone.utc)) == {}
    assert db.query(FujimotoEvidence).filter_by(kind="eps_identity",ticker="B").order_by(
        FujimotoEvidence.id.desc()).first().id == fresh.id
    module.validate_exclusion(frozen,"B",date(2026,10,21),datetime.now(timezone.utc))


def test_official_kosdaq_global_segment_is_collected_and_classified_as_ordinary(db,monkeypatch):
    import pandas as pd
    from maps.fujimoto.valuation import collect_metadata,security_inputs,security_kind
    from maps.fujimoto.repository import FujimotoRepository
    from maps.data.dart_financials import KST
    now = datetime.now(timezone.utc)
    day = now.astimezone(KST).date()
    FujimotoRepository(db).record_evidence("universe","*",now,now,{"ref_date":day,
        "members":[{"ticker":"GLOBAL","eligible":True}]})
    db.commit()
    row = dict(ISU_SRT_CD="GLOBAL",KIND_STKCERT_TP_NM="보통주",SECT_TP_NM="우량기업부",
               MKT_TP_NM="KOSDAQ GLOBAL",SECUGRP_NM="주권")
    monkeypatch.setattr("maps.data.krx_adapter.fetch_basic_information",lambda:pd.DataFrame([row]))
    result = collect_metadata(db,day)
    assert result["collected"] == 1 and result["missing_tickers"] == [] and result["status"] == "success"
    proof = security_inputs(db,day,datetime.now(timezone.utc))["GLOBAL"]
    assert security_kind(proof,"GLOBAL",day) == "ordinary"
