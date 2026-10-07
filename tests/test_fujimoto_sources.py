"""First-observed annual dividends and current universe evidence."""
from datetime import date, datetime, timezone
import pytest
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
