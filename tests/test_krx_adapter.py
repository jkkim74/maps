"""KRX adapter safety metadata tests."""

from __future__ import annotations

import datetime

from maps.data.krx_adapter import KRXAdapter, _classify_security_type


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
        "fetch_listing_dates",
        lambda: {"005930": datetime.date(1975, 6, 11), "014950": datetime.date(2025, 10, 27)},
    )

    metas = {m.ticker: m for m in KRXAdapter().get_security_meta(datetime.date(2026, 9, 7))}

    assert metas["005930"].listing_date == datetime.date(1975, 6, 11)
    assert metas["014950"].listing_date == datetime.date(2025, 10, 27)
    assert metas["999999"].listing_date is None
