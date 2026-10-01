from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from maps.common.db import Base


def rows(receipt="20260814000001"):
    return [dict(rcept_no=receipt, bsns_year="2026", reprt_code="11012", fs_div="CFS",
                 sj_div="IS", currency="KRW", account_id=account,
                 thstrm_add_amount=current, frmtrm_add_amount=prior,
                 thstrm_amount="999", frmtrm_amount="999")
            for account, current, prior in [("ifrs-full_Revenue", "1,200", "1,000"),
                                           ("dart_OperatingIncomeLoss", "120", "50")]]


def test_parser_uses_ytd_and_rejects_incompatible_pairs():
    from maps.data.dart_financials import parse_financials
    data = parse_financials(rows(), "20260814000001", "CFS", 2026, "11012")
    assert data["revenue"] == Decimal("1200")
    assert data["period_end"] == date(2026, 6, 30)
    for field, value in [("currency", "USD"), ("rcept_no", "other"), ("bsns_year", "2025"), ("thstrm_nm", "different quarter"), ("frmtrm_nm", "different prior period")]:
        bad = rows()
        bad[1][field] = value
        with pytest.raises(ValueError):
            parse_financials(bad, "20260814000001", "CFS", 2026, "11012")
    with pytest.raises(ValueError):
        parse_financials(rows() + [rows()[0]], "20260814000001", "CFS", 2026, "11012")


def test_annual_amounts_zero_profit_and_configured_closure(monkeypatch):
    from maps.data.dart_financials import parse_financials, available_date
    from maps.common.settings import get_settings
    annual = rows()
    for row in annual:
        row["reprt_code"] = "11011"
    annual[1]["thstrm_amount"] = "0"
    data = parse_financials(annual, "20260814000001", "CFS", 2026, "11011")
    assert data["revenue"] == Decimal("999")
    assert data["operating_profit"] == Decimal("0")
    monkeypatch.setattr(get_settings(), "maps_krx_closed_dates", "2026-08-18")
    assert available_date(date(2026, 8, 17), datetime(2026, 8, 17, tzinfo=timezone.utc)) == date(2026, 8, 19)


def test_repository_first_seen_revision_and_expiry():
    from maps.data.dart_financials import DartFinancialRepository
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        repo = DartFinancialRepository(db)
        seen = datetime(2026, 8, 17, 12, tzinfo=timezone.utc)
        receipt = "20260814000001"
        repo.record_receipt("005930", receipt, date(2026, 6, 30), date(2026, 8, 14), seen)
        repo.save("005930", receipt, "CFS", 2026, "11012", {"list": rows()}, seen)
        assert repo.get_as_of("005930", date(2026, 8, 17))["reason"] == "unavailable"
        assert repo.get_as_of("005930", date(2026, 8, 18))["revenue"] == Decimal("1200")
        repo.save("005930", receipt, "CFS", 2026, "11012", {"list": rows()}, datetime(2026, 9, 1, tzinfo=timezone.utc))
        assert repo.get_as_of("005930", date(2026, 8, 18))["revenue"] == Decimal("1200")
        repo.record_receipt("005930", "20260820000002", date(2026, 6, 30), date(2026, 8, 20), datetime(2026, 8, 20, tzinfo=timezone.utc))
        assert repo.get_as_of("005930", date(2026, 8, 21))["reason"] == "pending_revision"
        assert repo.get_as_of("005930", date(2027, 1, 1))["reason"] == "expired"


def test_http_bounds_transient_retry_and_auth_stop():
    from maps.data.dart_financials import DartClient, DartStop
    import requests
    ticks = [0.0]
    calls = []
    def sleep(seconds):
        ticks[0] += seconds
    def get(url, **kwargs):
        calls.append((ticks[0], kwargs))
        if len(calls) == 1:
            raise requests.Timeout()
        return type("Response", (), {"status_code": 200, "json": lambda self: {"status": "000", "list": []}})()
    client = DartClient("secret", get=get, monotonic=lambda: ticks[0], sleep=sleep)
    assert client.request("list.json")["status"] == "000"
    assert calls[1][0] - calls[0][0] >= 1
    assert calls[0][1]["timeout"] == (3, 15)
    client.requests = 500
    with pytest.raises(DartStop, match="budget"):
        client.request("list.json")
    auth = DartClient("secret", get=lambda *a, **k: type("Response", (), {"status_code": 200, "json": lambda self: {"status": "010"}})())
    with pytest.raises(DartStop, match="010"):
        auth.request("list.json")


def test_collector_preserves_pending_correction_and_only_explicit_ofs_fallback():
    from maps.data.dart_financials import DartFinancialCollector, DartFinancialRepository
    from maps.common.models import CandidateSnapshot, DartCollectionState
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(CandidateSnapshot(ref_date=date(2026, 8, 20), strategy_id="contrarian_quality_accumulation_v1", ticker="005930", name="Samsung", market="KOSPI"))
        db.commit()
        class Client:
            requests = 0
            calls = []
            def corp_codes(self):
                return {"005930": "00126380"}
            def request(self, endpoint, **params):
                self.calls.append((endpoint, params))
                if endpoint == "list.json":
                    return {"status": "000", "total_page": 1, "list": [
                        {"rcept_no": "20260820000002", "rcept_dt": "20260820", "report_nm": "[기재정정]반기보고서 (2026.06)"},
                        {"rcept_no": "20260814000001", "rcept_dt": "20260814", "report_nm": "반기보고서 (2026.06)"}]}
                return {"status": "000", "list": rows()}
        client = Client()
        result = DartFinancialCollector(db, "key", client=client, now=lambda: datetime(2026, 8, 20, 12, tzinfo=timezone.utc)).collect(date(2026, 8, 20))
        assert result["partial"] == 1
        assert DartFinancialRepository(db).get_as_of("005930", date(2026, 8, 21))["reason"] == "pending_revision"
        assert not any(params.get("fs_div") == "OFS" for _, params in client.calls)
        assert db.get(DartCollectionState, "005930").retry_at is not None


def test_paginated_correction_is_collected_without_retrying_superseded_receipt():
    from maps.data.dart_financials import DartFinancialCollector, DartFinancialRepository
    from maps.common.models import CandidateSnapshot, DartFilingReceipt
    from sqlalchemy import select
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(CandidateSnapshot(ref_date=date(2026, 8, 20), strategy_id="contrarian_quality_accumulation_v1", ticker="005930", name="Samsung", market="KOSPI"))
        db.commit()
        class Client:
            requests = 0
            def corp_codes(self):
                return {"005930": "00126380"}
            def request(self, endpoint, **params):
                if endpoint == "list.json":
                    receipt, day = ("20260820000002", "20260820") if params["page_no"] == 1 else ("20260814000001", "20260814")
                    return {"status": "000", "total_page": 2, "list": [dict(rcept_no=receipt, rcept_dt=day, report_nm="반기보고서 (2026.06)")]}
                if params["fs_div"] == "CFS":
                    return {"status": "013"}
                data = rows("20260820000002")
                for row in data:
                    row["fs_div"] = "OFS"
                return {"status": "000", "list": data}
        result = DartFinancialCollector(db, "key", client=Client(), now=lambda: datetime(2026, 8, 20, 12, tzinfo=timezone.utc)).collect(date(2026, 8, 20))
        assert result["status"] == "success"
        assert len(db.scalars(select(DartFilingReceipt)).all()) == 2
        assert DartFinancialRepository(db).get_as_of("005930", date(2026, 8, 21))["evidence"]["basis"] == "OFS"


def test_successful_ticker_is_checked_next_day_without_24_hour_drift(db):
    from maps.data.dart_financials import DartFinancialCollector
    from maps.common.models import CandidateSnapshot, DartCollectionState
    db.add(CandidateSnapshot(ref_date=date(2026, 8, 20), strategy_id="contrarian_quality_accumulation_v1",
                             ticker="005930", name="Samsung", market="KOSPI"))
    db.add(DartCollectionState(ticker="005930", status="success",
                              checked_at=datetime(2026, 8, 20, 12, 10, 2),
                              retry_at=datetime(2026, 8, 21, 12, 10, 2)))
    db.commit()
    class Client:
        requests = 0
        def corp_codes(self):
            return {"005930": "00126380"}
        def request(self, endpoint, **params):
            self.requests += 1
            return {"status": "013", "list": []}
    client = Client()
    result = DartFinancialCollector(db, "key", client=client,
        now=lambda: datetime(2026, 8, 21, 12, 10, tzinfo=timezone.utc)).collect(date(2026, 8, 21))
    assert result["collected"] == 1
    assert client.requests == 1
