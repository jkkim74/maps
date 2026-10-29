"""First-observed annual dividends and current universe evidence."""
from datetime import date, datetime, timezone
import pytest
from sqlalchemy.exc import SQLAlchemyError
from maps.common.exceptions import DataQualityError


def test_annual_dps_requires_exact_receipt_common_share_and_period():
    from maps.fujimoto.sources import annual_dps
    row = {"rcept_no": "20260301000001", "stock_knd": "보통주",
           "se": "주당 현금배당금(원)", "thstrm": "1,000", "stlm_dt": "2025-12-31"}
    assert annual_dps([row], row["rcept_no"], 2025) == 1000
    for change in ({"rcept_no": "later"}, {"stock_knd": "우선주"}, {"stlm_dt": "2024-12-31"}):
        with pytest.raises(DataQualityError):
            annual_dps([{**row, **change}], row["rcept_no"], 2025)


def test_comparability_evidence_cannot_backdate_observation(db):
    from maps.fujimoto.sources import record_comparability
    from maps.fujimoto.repository import FujimotoRepository
    now = datetime(2026, 10, 8, tzinfo=timezone.utc)
    for year, receipt in zip((2023, 2024, 2025), ("r1", "r2", "r3")):
        FujimotoRepository(db).record_evidence("annual_source", "AAA", now, now,
            {"receipt": receipt, "period_end": f"{year}-12-31"})
    evidence = record_comparability(db, "AAA", ["r1", "r2", "r3"],
        source_url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260301000001",
        document_hash="a" * 64, explanation="Annual statements confirm unchanged common share units.",
        owner=7, now=now)
    assert evidence.observed_at == now.replace(tzinfo=None)
    assert evidence.available_at > evidence.observed_at
    assert evidence.payload["receipts"] == ["r1", "r2", "r3"]
    assert len(evidence.payload["annual_source_ids"]) == 3
    with pytest.raises(DataQualityError, match="recorded_annual_receipts_required"):
        record_comparability(db, "AAA", ["r1", "r2", "unknown"],
            source_url="https://dart.fss.or.kr/filing", document_hash="a" * 64,
            explanation="Annual statements confirm unchanged common share units.", owner=7, now=now)


def test_negative_but_improving_profit_still_deteriorates(db, monkeypatch):
    from maps.fujimoto.sources import current_financial_status, DartFinancialRepository
    monkeypatch.setattr(DartFinancialRepository, "get_as_of", lambda *a: dict(
        revenue=110, prior_revenue=100, operating_profit=-1, prior_operating_profit=-2))
    assert current_financial_status(db, "AAA", date(2026, 10, 8)) == "deteriorated"


def test_screening_missing_universe_is_visible_and_never_rebuilt_from_metadata(db):
    from maps.fujimoto.sources import screen_market
    result = screen_market(db, date(2026, 10, 8), now=datetime(2026, 10, 8, 13, tzinfo=timezone.utc))
    assert result["reason"] == "missing_observed_universe"
    assert result["selected"] == 0


def test_empty_observed_universe_is_a_visible_source_failure(db):
    from maps.fujimoto.sources import screen_market
    from maps.fujimoto.repository import FujimotoRepository
    now = datetime(2026, 10, 28, 13, tzinfo=timezone.utc)
    repo = FujimotoRepository(db)
    universe = repo.record_evidence("universe", "*", now, now,
        {"ref_date": "2026-10-28", "members": [], "metadata_quality": {"status": "unavailable"}})
    db.commit()
    result = screen_market(db, date(2026, 10, 28), now=now)
    assert result["reason"] == "missing_observed_universe"
    assert result["universe_id"] == universe.id
    assert result["selected"] == 0
    assert repo.evidence_as_of("screen", "*", now)[-1].payload["reason"] == result["reason"]


def test_recorded_storage_gap_cannot_validate_as_complete_tape(db):
    from maps.fujimoto.replay import ReplayInput
    from maps.fujimoto.repository import FujimotoRepository
    from maps.fujimoto.validation import run_research, validation
    data = ReplayInput(1000000, {}, {}, provenance={"coverage_gaps": [{"reason": "tape_capacity_exhausted"}]})
    repo = FujimotoRepository(db)
    record = repo.store_replay(data, run_research(data))
    result = validation(repo, record.id)
    assert "recorded_coverage_gap" in result.reasons


def test_collector_resumes_all_market_instead_of_contrarian_candidates(db):
    from maps.common.models import SecurityMetadata
    from maps.fujimoto.sources import AnnualCollector
    class Client:
        requests = 0
        def corp_codes(self):
            return {"000001": "one", "000002": "two"}
        def request(self, endpoint, **params):
            self.requests += 1
            return {"status": "013"}
    db.add_all([SecurityMetadata(ticker=t, name=t, market="KOSPI", security_type="STOCK")
                for t in ("000001", "000002")])
    db.commit()
    client = Client()
    collector = AnnualCollector(db, "test", client=client)
    collector.collect(date(2026, 10, 8), batch=1)
    collector.collect(date(2026, 10, 8), batch=1)
    from maps.common.models import FujimotoEvidence
    assert [r.ticker for r in db.query(FujimotoEvidence).filter_by(kind="annual_collection").order_by(FujimotoEvidence.id)] == ["000001", "000002"]


def test_offline_research_cli_and_export_do_not_grant_execution(db, monkeypatch, tmp_path):
    import json
    from scripts.fujimoto_research import main, export_inputs
    from maps.fujimoto.replay import input_from_json
    output = tmp_path / "report.json"
    monkeypatch.setattr("sys.argv", ["fujimoto_research.py", "--demo", "--output", str(output)])
    main()
    assert json.loads(output.read_text(encoding="utf-8"))["execution_permission"] is False
    data = input_from_json(export_inputs(db, "isolated", 1000000))
    assert data.evidence == {} and data.tape == ()
    assert data.minimum_cash_fraction == .35


def test_export_never_rewrites_or_backfills_session_bars_from_later_history(db):
    import base64, json, zlib
    from maps.fujimoto.repository import FujimotoRepository
    from scripts.fujimoto_research import export_inputs
    repo = FujimotoRepository(db)
    def snapshot(day, prices):
        raw = {"prices": [{"date": d, "open": p, "high": p, "low": p, "close": p, "volume": 100}
                          for d, p in prices]}
        stamp = datetime.fromisoformat(day + "T13:00:00+00:00")
        repo.record_evidence("candidate", "AAA", stamp, stamp, {"ref_date": day, "rule": {},
            "raw_zlib": base64.b64encode(zlib.compress(json.dumps(raw).encode())).decode()})
    snapshot("2026-10-07", [("2026-10-07", 100)])
    snapshot("2026-10-08", [("2026-10-07", 90)])  # today's bar is missing
    snapshot("2026-10-12", [("2026-10-07", 80), ("2026-10-08", 95), ("2026-10-12", 110)])
    result = export_inputs(db, "account", 1000000)
    assert result["bars"]["2026-10-07"]["AAA"]["close"] == 100
    assert "AAA" not in result["bars"].get("2026-10-08", {})
    assert result["bars"]["2026-10-12"]["AAA"]["close"] == 110
    assert any(g["reason"] == "missing_session_bar" and g["date"] == "2026-10-08"
               for g in result["provenance"]["coverage_gaps"])


def test_export_rescreen_keeps_latest_rules_raw_ranking_and_source_ids(db):
    import base64, json, zlib
    from maps.fujimoto.repository import FujimotoRepository
    from scripts.fujimoto_research import export_inputs
    repo = FujimotoRepository(db)
    day = "2026-10-07"
    candidates, screens = [], []
    for hour, selected, price in ((7, False, 100), (13, True, 110)):
        stamp = datetime(2026, 10, 7, hour, tzinfo=timezone.utc)
        raw = {"revision": hour, "prices": [{"date": day, "open": price, "high": price,
                "low": price, "close": price, "volume": 100}]}
        candidates.append(repo.record_evidence("candidate", "AAA", stamp, stamp,
            {"ref_date": day, "rule": {"as_of": day, "selection_passed": selected, "close": price},
             "raw_zlib": base64.b64encode(zlib.compress(json.dumps(raw).encode())).decode()}).id)
        screens.append(repo.record_evidence("screen", "*", stamp, stamp,
            {"ref_date": day, "ranked": ["AAA"] if selected else []}).id)
    result = export_inputs(db, "account", 1000000)
    assert result["evidence"][day]["AAA"]["selection_passed"] is True
    assert result["evidence"][day]["AAA"]["close"] == 110
    assert result["screening"][day]["AAA"]["revision"] == 13
    assert result["candidate_order"][day] == ["AAA"]
    assert result["provenance"]["candidate_evidence"] == [candidates[-1]]
    assert result["provenance"]["screen_evidence"] == [screens[-1]]
    assert result["bars"][day]["AAA"]["close"] == 100
    assert result["provenance"]["bar_evidence"] == [candidates[0]]


def _screen_universe(db, tickers):
    """Persist complete causal sources and valid bars in the isolated test database."""
    from datetime import timedelta
    from maps.common.models import (ClassificationRun, ClassificationMember, DartFilingReceipt,
        DartFinancialSnapshot, HistoricalOHLCV, SecurityFundamental)
    from maps.fujimoto.repository import FujimotoRepository
    from maps.market.trading_rules import previous_trading_day
    day, now = date(2026, 9, 30), datetime(2026, 9, 30, 13, tzinfo=timezone.utc)
    repo = FujimotoRepository(db)
    repo.record_evidence("universe", "*", now, now, {"ref_date": day,
        "members": [{"ticker": t, "eligible": True} for t in tickers]})
    sector = ClassificationRun(kind="sector", provider="test", ref_date=day, status="complete",
        started_at=now, published_at=now, expected_tickers=tickers, catalog={"s": "sector"})
    db.add(sector)
    db.flush()
    dates = [day]
    for _ in range(100):
        dates.append(previous_trading_day(dates[-1]))
    for ticker in tickers:
        from maps.fujimoto.repository import fingerprint
        basic = {"ISU_SRT_CD":ticker,"KIND_STKCERT_TP_NM":"보통주","SECT_TP_NM":"",
                 "SECUGRP_NM":"주권","MKT_TP_NM":"KOSPI"}
        repo.record_evidence("security_source",ticker,now,now,{"ticker":ticker,"ref_date":day.isoformat(),
            "observed_at":now.isoformat(),"raw":basic,"raw_hash":fingerprint(basic),
            "source_url":"https://data.krx.co.kr/contents/MDC/MDI/mdiLoader"})
        db.add(ClassificationMember(run_id=sector.id, ticker=ticker, code="s", name="sector"))
        db.add(SecurityFundamental(ticker=ticker, date=day, per=10, updated_at=now))
        for year, value in ((2023, 100), (2024, 110), (2025, 120), (2026, 130)):
            annual = year != 2026
            period = date(year, 12, 31) if annual else date(2026, 6, 30)
            seen = datetime(year + 1, 3, 20) if annual else datetime(2026, 8, 14)
            available = seen.date() + timedelta(days=4)
            receipt = f"{ticker}-{year}"
            common = dict(ticker=ticker, receipt=receipt, period_end=period,
                publication_date=seen.date(), first_collected_at=seen, available_date=available)
            db.add(DartFilingReceipt(**common))
            snapshot = DartFinancialSnapshot(**common, basis="CFS", currency="KRW", raw_hash="a" * 64,
                raw_response={}, revenue=value, operating_profit=value / 10,
                prior_revenue=value - 10, prior_operating_profit=(value - 10) / 10)
            db.add(snapshot)
            db.flush()
            if annual:
                repo.record_evidence("annual_source", ticker, seen, seen,
                    {"receipt": receipt, "dividend_per_share": value / 100})
        review_at = datetime(2026, 4, 1)
        repo.record_evidence("comparability", ticker, review_at, review_at,
            {"receipts": [f"{ticker}-{y}" for y in (2023, 2024, 2025)], "share_basis": "reviewed"})
        db.add_all(HistoricalOHLCV(ticker=ticker, date=d, open=4160, high=4180, low=4100,
            close=4150, volume=70831, adj_close=4150) for d in dates)
    db.commit()
    return day, now


def _candidate_raw(candidate):
    """Decode exactly the persisted original candidate source."""
    import base64, json, zlib
    return json.loads(zlib.decompress(base64.b64decode(candidate.payload["raw_zlib"])))


def test_screen_bad_ticker_preserves_original_bars_and_continues_to_valid_ticker(db):
    from maps.common.models import FujimotoEvidence, HistoricalOHLCV
    from maps.fujimoto.domain import CycleState, Mode, RuleEvidence, evaluate
    from maps.fujimoto.replay import screening_evidence
    from maps.fujimoto.sources import screen_market
    day, now = _screen_universe(db, ["095570", "005930"])
    # Original production bars: close exceeds high; screening must never repair these.
    for bar_day, values in ((date(2026, 9, 21), (4160, 4180, 4100, 4185, 70205)),
                            (day, (4315, 4340, 4270, 4370, 45304))):
        bar = db.query(HistoricalOHLCV).filter_by(ticker="095570", date=bar_day).one()
        bar.open, bar.high, bar.low, bar.close, bar.volume = values
    db.commit()
    result = screen_market(db, day, now=now)
    assert result["screened"] == 2 and result["selected"] == 1
    assert result["ranked"] == ["005930"]
    assert result["blocked"] == 1
    assert result["blocking_reasons"]["invalid_ohlcv_range"] == 1
    candidates = db.query(FujimotoEvidence).filter_by(kind="candidate").order_by(FujimotoEvidence.id).all()
    assert [r.ticker for r in candidates] == ["095570", "005930"]
    bad, good = candidates
    rule = RuleEvidence(**{**bad.payload["rule"], "as_of": day,
        "blocking_reasons": tuple(bad.payload["rule"]["blocking_reasons"])})
    assert rule.close is None and rule.selection_passed is False
    assert rule.blocking_reasons == ("invalid_ohlcv_range",)
    assert all(evaluate(mode, rule, CycleState()).action != "buy" for mode in Mode)
    raw = _candidate_raw(bad)
    assert [(p["open"], p["high"], p["low"], p["close"], p["volume"])
        for p in raw["prices"] if p["date"] in ("2026-09-21", "2026-09-30")] == [
            (4160, 4180, 4100, 4185, 70205), (4315, 4340, 4270, 4370, 45304)]
    with pytest.raises(DataQualityError, match="invalid_ohlcv_range"):
        screening_evidence(raw, bad.ticker, day)
    assert screening_evidence(_candidate_raw(good), good.ticker, day).selection_passed
    screen = db.query(FujimotoEvidence).filter_by(kind="screen").one()
    assert screen.payload["blocking_reasons"]["invalid_ohlcv_range"] == 1
    from maps.common.settings import MapsSettings
    from maps.fujimoto.service import FujimotoService
    service = FujimotoService(db, settings=MapsSettings(_env_file=None, maps_broker_mode="mock"))
    assert "usable_screen_candidate_source_missing" in service._paper_readiness("isolated", now, ticker="095570")
    assert "usable_screen_candidate_source_missing" not in service._paper_readiness("isolated", now, ticker="005930")


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf")])
def test_screen_nonfinite_prices_are_serializable_blocked_sources(db, monkeypatch, invalid):
    import json
    from maps.common.models import FujimotoEvidence
    from maps.data.ohlcv_repo import HistoricalOHLCVRepository
    from maps.fujimoto.replay import screening_evidence
    from maps.fujimoto.sources import screen_market
    day, now = _screen_universe(db, ["095570"])
    original = HistoricalOHLCVRepository.to_dataframe
    def malformed(self, ticker, **kwargs):
        frame = original(self, ticker, **kwargs)
        frame.iloc[-1, frame.columns.get_loc("close")] = invalid
        return frame
    # SQLite normalizes NaN to NULL, so inject the repository-shaped numeric frame.
    monkeypatch.setattr(HistoricalOHLCVRepository, "to_dataframe", malformed)
    result = screen_market(db, day, now=now)
    assert result["selected"] == 0 and result["ranked"] == [] and result["blocked"] == 1
    assert result["blocking_reasons"] == {"nonfinite_ohlcv": 1}
    candidate = db.query(FujimotoEvidence).filter_by(kind="candidate").one()
    json.dumps(candidate.payload, allow_nan=False)
    raw = _candidate_raw(candidate)
    json.dumps(raw, allow_nan=False)
    assert isinstance(raw["prices"][-1]["close"], str)
    assert candidate.payload["rule"]["close"] is None
    with pytest.raises(DataQualityError, match="nonfinite_ohlcv"):
        screening_evidence(raw, candidate.ticker, day)


def test_screen_all_invalid_candidates_records_complete_blocked_counts(db):
    from maps.common.models import FujimotoEvidence, HistoricalOHLCV
    from maps.fujimoto.sources import screen_market
    day, now = _screen_universe(db, ["095570", "005930"])
    for bar in db.query(HistoricalOHLCV).filter_by(date=day):
        bar.close = 4185
    db.commit()
    result = screen_market(db, day, now=now)
    assert result["screened"] == 2 and result["blocked"] == 2
    assert result["selected"] == 0 and result["ranked"] == []
    assert result["blocking_reasons"] == {"invalid_ohlcv_range": 2}
    assert db.query(FujimotoEvidence).filter_by(kind="candidate").count() == 2


@pytest.mark.parametrize("failure", [SQLAlchemyError("database defect"), DataQualityError("persistence defect")])
def test_screen_candidate_persistence_errors_propagate(db, monkeypatch, failure):
    from maps.common.models import FujimotoEvidence
    from maps.fujimoto.repository import FujimotoRepository
    from maps.fujimoto.sources import screen_market
    day, now = _screen_universe(db, ["005930"])
    original = FujimotoRepository.record_evidence
    def persist(self, kind, *args, **kwargs):
        if kind == "candidate":
            raise failure
        return original(self, kind, *args, **kwargs)
    monkeypatch.setattr(FujimotoRepository, "record_evidence", persist)
    with pytest.raises(type(failure), match=str(failure)):
        screen_market(db, day, now=now)
    assert db.query(FujimotoEvidence).filter_by(kind="screen").count() == 0


def test_screen_programming_errors_in_rule_builder_propagate(db, monkeypatch):
    from maps.common.models import FujimotoEvidence
    from maps.fujimoto.sources import screen_market
    day, now = _screen_universe(db, ["005930"])
    def broken(*args, **kwargs):
        raise RuntimeError("programming defect")
    monkeypatch.setattr("maps.fujimoto.sources.screening_evidence", broken)
    with pytest.raises(RuntimeError, match="programming defect"):
        screen_market(db, day, now=now)
    assert db.query(FujimotoEvidence).filter_by(kind="screen").count() == 0


def test_targeted_annual_collection_validates_observed_universe_and_resumes(db):
    from maps.fujimoto.repository import FujimotoRepository
    from maps.fujimoto.sources import AnnualCollector
    from maps.data.dart_financials import DartStop
    now = datetime(2026, 10, 8, 13, tzinfo=timezone.utc)
    repo = FujimotoRepository(db)
    repo.record_evidence("universe", "*", now, now, {"ref_date": "2026-10-08", "members": [
        {"ticker": "A", "eligible": True}, {"ticker": "B", "eligible": True},
        {"ticker": "C", "eligible": False}]})
    db.commit()
    class Client:
        requests = 0
        def corp_codes(self):
            return {"A": "A", "B": "B"}
        def request(self, endpoint, **params):
            self.requests += 1
            raise DartStop("budget_exhausted")
    collector = AnnualCollector(db, "test", client=Client(), now=lambda: now)
    first = collector.collect(now.date(), batch=1, tickers=["A", "B", "C", "unknown"])
    second = collector.collect(now.date(), batch=1, tickers=["A", "B"])
    assert first["rejected_targets"] == ["C", "unknown"]
    assert first["attempted"] == ["A"] and second["attempted"] == ["B"]
    assert first["transient"] == 1 and first["missing_source"] == 0


def test_valuation_source_review_is_bound_to_actual_observation_and_newer_source(db):
    from maps.fujimoto.sources import record_valuation_source, record_valuation_review, valuation_inputs
    now = datetime.now(timezone.utc)
    raw = {"ticker": "A", "PER": "-"}
    source = record_valuation_source(db, "A", now.astimezone(__import__('maps.data.dart_financials', fromlist=['KST']).KST).date(),
        per=None, raw=raw, source_url="https://data.krx.co.kr/actual", document_hash="a" * 64)
    kwargs = dict(source_id=source.id, contract_url="https://data.krx.co.kr/contract",
        contract_hash="b" * 64, explanation="Official semantics and exact ticker response establish non-applicability.", owner=1)
    review = record_valuation_review(db, "A", source.payload["ref_date"], **kwargs)
    assert review.observed_at >= source.observed_at
    vals = valuation_inputs(db, now.date(), datetime.now(timezone.utc), [])
    assert vals[0]["status"] == "verified_not_applicable"
    record_valuation_source(db, "A", now.date(), per=12, raw={"PER": 12},
        source_url="https://data.krx.co.kr/actual", document_hash="c" * 64)
    assert valuation_inputs(db, now.date(), datetime.now(timezone.utc), [])[0]["per"] == 12
    with pytest.raises(DataQualityError):
        record_valuation_review(db, "B", source.payload["ref_date"], **kwargs)
    with pytest.raises(DataQualityError):
        record_valuation_review(db, "A", source.payload["ref_date"], **{**kwargs, "contract_hash": "bad"})


def test_collect_annual_cli_reports_bounded_targets_without_execution(monkeypatch, tmp_path):
    import json
    from scripts.fujimoto_research import main
    from maps.fujimoto.sources import AnnualCollector
    calls = []
    def collect(self, day, **kwargs):
        calls.append((day, kwargs))
        return {"attempted": [], "rejected_targets": ["BAD"]}
    monkeypatch.setattr(AnnualCollector, "collect", collect)
    output = tmp_path / "annual.json"
    monkeypatch.setattr("sys.argv", ["fujimoto_research.py", "--collect-annual", "--database", "sqlite://",
        "--tickers", "A,BAD", "--batch", "2", "--output", str(output)])
    main()
    assert calls[0][1] == {"batch": 2, "tickers": ["A", "BAD"]}
    assert json.loads(output.read_text())["execution_permission"] is False


def test_valuation_review_provenance_rejects_future_and_modified_raw(db):
    from copy import deepcopy
    from datetime import timedelta
    from maps.fujimoto.sources import record_valuation_source, record_valuation_review, valuation_inputs
    from maps.fujimoto.evidence import ValuationRecord
    now = datetime.now(timezone.utc)
    source = record_valuation_source(db, "A", now.date(), per=None, raw={"PER": "-"},
        source_url="https://data.krx.co.kr/value", document_hash="a" * 64)
    record_valuation_review(db, "A", now.date(), source_id=source.id,
        contract_url="https://data.krx.co.kr/contract", contract_hash="b" * 64,
        explanation="Verified exact ticker and provider non-applicability contract.", owner=7)
    row = valuation_inputs(db, now.date(), datetime.now(timezone.utc), [])[0]
    def decode(value):
        return ValuationRecord(**{**value, "ref_date": date.fromisoformat(value["ref_date"]),
                                  "available_at": datetime.fromisoformat(value["available_at"])})
    assert decode(row).status == "verified_not_applicable"
    for part in ("source", "review"):
        bad = deepcopy(row)
        bad["provenance"][part]["observed_at"] = (now + timedelta(days=1)).isoformat()
        with pytest.raises(DataQualityError, match="invalid_valuation_provenance"):
            decode(bad)
    bad = deepcopy(row)
    bad["provenance"]["source"]["raw"] = {"PER": 10}
    with pytest.raises(DataQualityError, match="invalid_valuation_provenance"):
        decode(bad)
    with pytest.raises(DataQualityError):
        record_valuation_source(db, "A", now.date() + timedelta(days=1), per=None, raw={"PER": "-"},
            source_url="https://data.krx.co.kr/value", document_hash="a" * 64)


@pytest.mark.parametrize("contract", ["https://krx.co.kr.evil.test/doc", "https://user@data.krx.co.kr/doc",
    "https://data.krx.co.kr:444/doc", "https://dart.fss.or.kr/doc", "http://data.krx.co.kr/doc"])
def test_per_review_rejects_nonofficial_or_different_provider_contract(db, contract):
    from maps.fujimoto.sources import record_valuation_source, record_valuation_review
    now = datetime.now(timezone.utc)
    source = record_valuation_source(db, "A", now.date(), per=None, raw={"PER": "-"},
        source_url="https://data.krx.co.kr/value", document_hash="a" * 64)
    with pytest.raises(DataQualityError, match="invalid_valuation_provenance"):
        record_valuation_review(db, "A", now.date(), source_id=source.id,
            contract_url=contract, contract_hash="b" * 64,
            explanation="Exact ticker non-applicability verified against source contract.", owner=1)


def test_target_selection_prioritizes_latest_candidate_without_growth_failure(db):
    from maps.fujimoto.sources import AnnualCollector
    from maps.fujimoto.repository import FujimotoRepository
    now = datetime(2026, 10, 8, 13, tzinfo=timezone.utc)
    repo = FujimotoRepository(db)
    repo.record_evidence("universe", "*", now, now, {"ref_date": now.date(), "members": [
        {"ticker": t, "eligible": True} for t in ("A", "B")]})
    for ticker, reasons in (("A", ["annual_growth_failed"]), ("B", ["dividend_failed"]), ("B", ["missing_dividend"])):
        repo.record_evidence("candidate", ticker, now, now, {"rule": {"blocking_reasons": reasons}})
    db.commit()
    class Client:
        requests = 0
        def corp_codes(self):
            return {}
    collector = AnnualCollector(db, "test", client=Client(), now=lambda: now)
    assert collector.collect(now.date(), batch=1, tickers=["A", "B"])["attempted"] == ["B"]


def test_targeted_annual_priority_projects_only_target_reasons_at_cutoff(db):
    """A tiny collection must not fetch every candidate's compressed source blob."""
    from datetime import timedelta
    from sqlalchemy import event
    from maps.common.models import FujimotoEvidence
    from maps.fujimoto.repository import FujimotoRepository
    from maps.fujimoto.sources import AnnualCollector

    now = datetime(2026, 10, 8, 13, tzinfo=timezone.utc)
    before = now - timedelta(days=1)
    later = now + timedelta(seconds=1)
    repo = FujimotoRepository(db)
    repo.record_evidence("universe", "*", before, before,
        {"ref_date": now.date(), "members": [{"ticker": t, "eligible": True} for t in "ABCD"]})
    # Preserve current latest-by-ID behavior even if observation times differ.
    for ticker, reasons, observed, available in [
        ("A", ["annual_growth_failed"], before, before),
        ("A", ["missing_dividend"], before - timedelta(days=1), before),
        ("B", ["dividend_failed"], before, before),
        ("B", [], later, later),
        ("C", ["ineligible_security"], before, before),
        ("C", [], before, later),
    ]:
        repo.record_evidence("candidate", ticker, observed, available,
            {"rule": {"blocking_reasons": reasons}, "raw_zlib": "large-source" * 1000})
    for index in range(32):
        repo.record_evidence("candidate", f"unrelated-{index}", before, before,
            {"rule": {"blocking_reasons": []}, "raw_zlib": "unused" * 5000})
    repo.record_evidence("annual_collection", "A", before, before, {"status": "partial"})
    repo.record_evidence("candidate", "D", before, before, {"raw_zlib": "no-rule"})
    db.commit()
    db.expunge_all()
    selected_columns = []
    priority_queries = []
    loaded_candidates = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        compiled = context.compiled
        if compiled is not None and "candidate" in compiled.params.values():
            selected_columns.append(list(compiled.statement.selected_columns))
            priority_queries.append((compiled.params, context.execution_options))

    def loaded(row, context):
        if row.kind == "candidate":
            loaded_candidates.append(row.ticker)

    class Client:
        requests = 0

        def corp_codes(self):
            return {}

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", capture)
    event.listen(FujimotoEvidence, "load", loaded)
    try:
        result = AnnualCollector(db, "test", client=Client(), now=lambda: now).collect(
            now.date(), batch=4, tickers=["A", "B", "C", "D", "unknown"])
    finally:
        event.remove(engine, "before_cursor_execute", capture)
        event.remove(FujimotoEvidence, "load", loaded)
    assert result["attempted"] == ["D", "A", "B", "C"]
    assert result["rejected_targets"] == ["unknown"]
    assert selected_columns
    payload = FujimotoEvidence.__table__.c.payload
    assert not any(column.compare(payload) for columns in selected_columns for column in columns)
    assert loaded_candidates == []
    assert all(any(isinstance(value, (list, tuple)) and set(value) == set("ABCD")
                   for value in parameters.values()) for parameters, _ in priority_queries)
    assert all(options.get("stream_results") and options.get("yield_per", 0) > 0
               for _, options in priority_queries)


def test_late_review_of_old_source_never_overrides_newer_observation(db):
    from maps.fujimoto.sources import record_valuation_source, record_valuation_review, valuation_inputs
    now = datetime.now(timezone.utc)
    old = record_valuation_source(db, "A", now.date(), per=None, raw={"PER": "-"},
        source_url="https://data.krx.co.kr/value", document_hash="a" * 64)
    latest = record_valuation_source(db, "A", now.date(), per=12, raw={"PER": "12"},
        source_url="https://data.krx.co.kr/value", document_hash="b" * 64)
    record_valuation_review(db, "A", now.date(), source_id=old.id,
        contract_url="https://data.krx.co.kr/contract", contract_hash="c" * 64,
        explanation="Official non-applicability contract applies only to older observation.", owner=1)
    value = valuation_inputs(db, now.date(), datetime.now(timezone.utc), [])[0]
    assert value["per"] == 12 and value["provenance"]["source_id"] == latest.id


def test_frozen_v2_excludes_only_verified_na_while_legacy_retains_null_block(db):
    import pandas as pd
    from maps.fujimoto.sources import record_valuation_source, record_valuation_review, valuation_inputs
    from maps.fujimoto.replay import screening_evidence
    from maps.data.dart_financials import KST
    now = datetime.now(timezone.utc)
    day = now.astimezone(KST).date()
    source = record_valuation_source(db, "B", day, per=None, raw={"PER": "-"},
        source_url="https://data.krx.co.kr/value", document_hash="a" * 64)
    record_valuation_review(db, "B", day, source_id=source.id,
        contract_url="https://data.krx.co.kr/contract", contract_hash="b" * 64,
        explanation="Exact ticker and date verified against official provider contract.", owner=1)
    values = valuation_inputs(db, day, datetime.now(timezone.utc), [dict(ticker="A", ref_date=day,
        available_at=now, per=10)])
    raw = {"annual_records": [], "valuations": values,
        "sectors": {"ref_date": day.isoformat(), "available_at": now.isoformat(),
                    "memberships": [["A", "s"], ["B", "s"]]},
        "prices": [{"date": day.isoformat(), "open": 10, "high": 11, "low": 9, "close": 10, "volume": 1}],
        "eligible": True}
    assert "incomplete_sector_valuation" in screening_evidence(raw, "A", day).blocking_reasons
    assert "incomplete_sector_valuation" not in screening_evidence({**raw, "screening_version": 2}, "A", day).blocking_reasons
    raw["valuations"] = [{**r, "status": "unknown", "provenance": None} if r["ticker"] == "B" else r for r in values]
    assert "incomplete_sector_valuation" in screening_evidence({**raw, "screening_version": 2}, "A", day).blocking_reasons
