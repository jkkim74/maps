"""KRX adapter safety metadata tests."""

from __future__ import annotations

import datetime
import pytest

from maps.data.krx_adapter import KRXAdapter, _classify_security_type


@pytest.fixture(autouse=True)
def _disable_import_time_krx_login(monkeypatch):
    # pykrx webio attempts login during import, before the adapter guard exists.
    monkeypatch.setenv("KRX_ID", "")
    monkeypatch.setenv("KRX_PW", "")


def test_security_type_classification() -> None:
    assert _classify_security_type("ABC스팩1호") == "SPAC"
    assert _classify_security_type("KODEX 200 ETF") == "ETF"
    assert _classify_security_type("삼성전자") == "STOCK"


def test_managed_tickers_manual_override(monkeypatch) -> None:
    monkeypatch.setenv("MAPS_MANAGED_TICKERS", "005930, 000660")

    adapter = KRXAdapter()

    assert adapter.get_managed_list(datetime.date(2024, 6, 1)) == ["000660", "005930"]


# --- 상장일 적재 (KRX 전종목 기본정보) -----------------------------------------
#
# 운영 security_metadata.listing_date 가 2,790행 전부 NULL 이었고(2026-09-07 발견),
# 상한가 V1 자격 판정이 fail-closed 라 후보가 한 건도 수락되지 않았다. pykrx 의
# ticker-list 엔드포인트는 상장일을 주지 않으므로 [12005] 전종목 기본정보에서 채운다.


def _basic_info_frame(rows: list[dict]) -> "pd.DataFrame":
    import pandas as pd

    return pd.DataFrame(rows)


def test_listing_dates_from_frame_parses_krx_dates_and_skips_bad_rows() -> None:
    from maps.data.krx_adapter import _listing_dates_from_frame

    frame = _basic_info_frame(
        [
            {"ISU_SRT_CD": "005930", "LIST_DD": "1975/06/11"},
            {"ISU_SRT_CD": "014950", "LIST_DD": "2025/10/27"},
            {"ISU_SRT_CD": "000001", "LIST_DD": ""},
            {"ISU_SRT_CD": "000002", "LIST_DD": "not-a-date"},
            {"ISU_SRT_CD": "000003", "LIST_DD": None},
        ]
    )

    assert _listing_dates_from_frame(frame) == {
        "005930": datetime.date(1975, 6, 11),
        "014950": datetime.date(2025, 10, 27),
    }


def test_fetch_listing_dates_installs_the_login_guard_before_pykrx(monkeypatch) -> None:
    """루트 CLAUDE.md 제약 8 — pykrx 를 건드리기 전에 회로차단기를 설치한다."""
    import maps.data.krx_adapter as mod
    from pykrx.website.krx.market import core as krx_core

    calls: list[str] = []
    monkeypatch.setattr(mod, "ensure_krx_login_guard", lambda: calls.append("guard") or True)

    class _Basic:
        def fetch(self, mktId: str = "ALL", segTpCd: str = "ALL"):
            calls.append(f"fetch:{mktId}")
            return _basic_info_frame([{"ISU_SRT_CD": "005930", "LIST_DD": "1975/06/11"}])

    monkeypatch.setattr(krx_core, "전종목기본정보", _Basic)

    assert mod.fetch_listing_dates() == {"005930": datetime.date(1975, 6, 11)}
    assert calls == ["guard", "fetch:ALL"]


def test_fetch_listing_dates_fails_soft_with_a_warning(monkeypatch, caplog) -> None:
    """KRX 조회가 깨져도 일일 수집은 계속된다 — 값은 비고 하류가 fail-closed 로 막는다."""
    import maps.data.krx_adapter as mod
    from pykrx.website.krx.market import core as krx_core

    monkeypatch.setattr(mod, "ensure_krx_login_guard", lambda: True)

    class _Broken:
        def fetch(self, mktId: str = "ALL", segTpCd: str = "ALL"):
            raise RuntimeError("LOGOUT")

    monkeypatch.setattr(krx_core, "전종목기본정보", _Broken)

    with caplog.at_level("WARNING", logger="maps.data.krx_adapter"):
        assert mod.fetch_listing_dates() == {}

    assert any("상장일" in rec.getMessage() and "LOGOUT" in rec.getMessage() for rec in caplog.records)


def test_get_security_meta_carries_the_listing_date(monkeypatch) -> None:
    """메타 행마다 상장일이 실리고, 모르는 종목은 None 으로 남는다(덮어쓰지 않는다)."""
    import maps.data.krx_adapter as mod
    from pykrx import stock as krx_stock

    monkeypatch.setattr(mod, "ensure_krx_login_guard", lambda: True)
    monkeypatch.setattr(
        krx_stock,
        "get_market_ticker_list",
        lambda date_str, market="KOSPI": ["005930"] if market == "KOSPI" else ["014950", "999999"],
    )
    monkeypatch.setattr(krx_stock, "get_market_ticker_name", lambda ticker: f"이름{ticker}")
    monkeypatch.setattr(
        mod,
        "fetch_basic_information",
        lambda: _basic_info_frame([
            {"ISU_SRT_CD": "005930", "ISU_ABBRV": "삼성전자", "LIST_DD": "1975/06/11"},
            {"ISU_SRT_CD": "014950", "ISU_ABBRV": "종목", "LIST_DD": "2025/10/27"},
        ]),
    )

    metas = {m.ticker: m for m in KRXAdapter().get_security_meta(datetime.date(2026, 9, 7))}

    assert metas["005930"].listing_date == datetime.date(1975, 6, 11)
    assert metas["014950"].listing_date == datetime.date(2025, 10, 27)
    assert metas["999999"].listing_date is None


def _metadata_sources(monkeypatch, rows, membership, fallback):
    import maps.data.krx_adapter as mod
    from pykrx import stock
    from pykrx.website.krx.market import core

    monkeypatch.setattr(mod, "ensure_krx_login_guard", lambda: True)
    class Basic:
        def fetch(self, market):
            return _basic_info_frame(rows)
    monkeypatch.setattr(core, "전종목기본정보", Basic)
    def members(day, market):
        value = membership[market]
        if isinstance(value, Exception):
            raise value
        return value
    monkeypatch.setattr(stock, "get_market_ticker_list", members)
    monkeypatch.setattr(stock, "get_market_ticker_name", lambda ticker: fallback.get(ticker))


def test_metadata_bad_name_does_not_truncate_market_and_fresh_name_wins(monkeypatch):
    import pandas as pd
    _metadata_sources(monkeypatch,
        [{"ISU_SRT_CD": "000001", "ISU_ABBRV": "Fresh", "LIST_DD": "2020/01/02"}],
        {"KOSPI": ["000001", "000002", "000003"], "KOSDAQ": ["00AB10"]},
        {"000001": "Stale", "000002": pd.DataFrame(), "000003": "After", "00AB10": "Alpha"})
    result = KRXAdapter().get_security_meta_result(datetime.date(2026, 9, 30))
    assert [(m.ticker, m.name) for m in result.items] == [
        ("000001", "Fresh"), ("000003", "After"), ("00AB10", "Alpha")]
    assert result.items[1].listing_date is None
    assert result.markets["KOSPI"]["missing_tickers"] == ["000002"]
    assert result.markets["KOSPI"]["listing_date_missing_tickers"] == ["000003"]
    assert result.status == "partial"
    assert not result.candidate_ready


def test_metadata_coverage_boundary_is_per_market(monkeypatch):
    tickers = [f"{i:06}" for i in range(20)]
    names = {ticker: "Name" for ticker in tickers[:-1]}
    _metadata_sources(monkeypatch, [], {"KOSPI": tickers, "KOSDAQ": ["00AB10"]}, {**names, "00AB10": "Alpha"})
    result = KRXAdapter().get_security_meta_result(datetime.date(2026, 9, 30))
    assert result.markets["KOSPI"]["coverage_ratio"] == .95
    assert result.candidate_ready
    names.pop(tickers[-2])
    _metadata_sources(monkeypatch, [], {"KOSPI": tickers, "KOSDAQ": ["00AB10"]}, {**names, "00AB10": "Alpha"})
    assert not KRXAdapter().get_security_meta_result(datetime.date(2026, 9, 30)).candidate_ready


def test_metadata_failed_or_empty_membership_is_unavailable(monkeypatch):
    for membership in ([], RuntimeError("membership failed")):
        _metadata_sources(monkeypatch, [], {"KOSPI": ["005930"], "KOSDAQ": membership}, {"005930": "Samsung"})
        result = KRXAdapter().get_security_meta_result(datetime.date(2026, 9, 30))
        assert result.status == "unavailable"
        assert not result.candidate_ready
        assert result.markets["KOSDAQ"]["error"]


def test_listing_dates_bad_scalar_row_does_not_drop_later_rows():
    import pandas as pd
    from maps.data.krx_adapter import _listing_dates_from_frame
    frame = _basic_info_frame([
        {"ISU_SRT_CD": pd.NA, "LIST_DD": "2020/01/01"},
        {"ISU_SRT_CD": "00AB10", "LIST_DD": "2020/01/02"},
    ])
    assert _listing_dates_from_frame(frame) == {"00AB10": datetime.date(2020, 1, 2)}


def test_metadata_rejects_blank_and_ticker_fallback_names(monkeypatch):
    _metadata_sources(monkeypatch, [],
        {"KOSPI": ["000001", "000002", "000003", "000004"], "KOSDAQ": ["00AB10"]},
        {"000001": " ", "000002": "000002", "000003": float("nan"), "000004": "Good", "00AB10": "Alpha"})
    result = KRXAdapter().get_security_meta_result(datetime.date(2026, 9, 30))
    assert [item.ticker for item in result.items] == ["000004", "00AB10"]
    assert result.markets["KOSPI"]["valid_count"] == 1


def test_metadata_readiness_rejects_inconsistent_counts():
    from maps.data.krx_adapter import MetadataCollection, SecurityMeta
    item = SecurityMeta("005930", "Samsung", "KOSPI", "STOCK")
    quality = {"expected_count": 100, "valid_count": 1, "coverage_ratio": 1.0, "error": None}
    result = MetadataCollection([item], {"KOSPI": quality, "KOSDAQ": quality}, "complete")
    assert not result.candidate_ready


def test_metadata_membership_uses_requested_date_not_current_basic_rows(monkeypatch):
    from pykrx import stock
    requested = datetime.date(2024, 1, 2)
    _metadata_sources(monkeypatch, [
        {"ISU_SRT_CD": "005930", "ISU_ABBRV": "Current name", "LIST_DD": "1975/06/11"},
        {"ISU_SRT_CD": "999999", "ISU_ABBRV": "New listing", "LIST_DD": "2026/01/01"},
    ], {"KOSPI": ["005930"], "KOSDAQ": ["00AB10"]}, {"00AB10": "Alpha"})
    def dated_membership(day, market):
        assert day == "20240102"
        return ["005930"] if market == "KOSPI" else ["00AB10"]
    monkeypatch.setattr(stock, "get_market_ticker_list", dated_membership)
    result = KRXAdapter().get_security_meta_result(requested)
    assert [item.ticker for item in result.items] == ["005930", "00AB10"]


def test_basic_endpoint_failure_uses_valid_fallback_and_records_missing_dates(monkeypatch):
    import maps.data.krx_adapter as mod
    _metadata_sources(monkeypatch, [], {"KOSPI": ["005930"], "KOSDAQ": ["00AB10"]},
                      {"005930": "Samsung", "00AB10": "Alpha"})
    def broken():
        raise RuntimeError("basic failed")
    monkeypatch.setattr(mod, "fetch_basic_information", broken)
    result = KRXAdapter().get_security_meta_result(datetime.date(2026, 9, 30))
    assert result.status == "partial"
    assert result.candidate_ready
    assert all(item.listing_date is None for item in result.items)
    assert "basic failed" in result.markets["KOSPI"]["basic_information_error"]


def test_malformed_membership_entry_does_not_discard_other_tickers(monkeypatch):
    _metadata_sources(monkeypatch, [],
        {"KOSPI": ["005930", ["bad"], "000660"], "KOSDAQ": ["00AB10"]},
        {"005930": "Samsung", "000660": "Hynix", "00AB10": "Alpha"})
    result = KRXAdapter().get_security_meta_result(datetime.date(2026, 9, 30))
    assert [item.ticker for item in result.items] == ["005930", "000660", "00AB10"]
    assert result.markets["KOSPI"]["expected_count"] == 3
    assert result.markets["KOSPI"]["valid_count"] == 2
