"""Naver 펀더멘털 어댑터 테스트 (KRX MDC 차단 대체 경로)."""

from __future__ import annotations

import datetime

from maps.data.naver_fundamental import NaverFundamentalAdapter, _to_number

REF = datetime.date(2026, 6, 23)


def test_to_number_strips_units_and_commas() -> None:
    assert _to_number("26.57배") == 26.57
    assert _to_number("12,372원") == 12372.0
    assert _to_number("0.51%") == 0.51
    assert _to_number("1,921,964") == 1921964.0


def test_to_number_returns_none_for_missing() -> None:
    for missing in (None, "", "-", "N/A"):
        assert _to_number(missing) is None


def test_get_one_parses_total_infos(monkeypatch) -> None:
    payload = {
        "totalInfos": [
            {"code": "per", "value": "26.57배"},
            {"code": "eps", "value": "12,372원"},
            {"code": "pbr", "value": "4.57배"},
            {"code": "bps", "value": "71,907원"},
            {"code": "dividendYieldRatio", "value": "0.51%"},
            {"code": "dividend", "value": "1,668원"},
        ]
    }

    class _Resp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return payload

    adapter = NaverFundamentalAdapter()
    monkeypatch.setattr(adapter._session, "get", lambda *a, **k: _Resp())

    row = adapter.get_one("005930", REF)

    assert row is not None
    assert row.ticker == "005930"
    assert row.date == REF
    assert row.per == 26.57
    assert row.eps == 12372.0
    assert row.pbr == 4.57
    assert row.bps == 71907.0
    assert row.div == 0.51
    assert row.dps == 1668.0


def test_get_one_returns_none_on_empty_payload(monkeypatch) -> None:
    class _Resp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"totalInfos": []}

    adapter = NaverFundamentalAdapter()
    monkeypatch.setattr(adapter._session, "get", lambda *a, **k: _Resp())

    assert adapter.get_one("000000", REF) is None


def test_get_one_returns_none_on_request_error(monkeypatch) -> None:
    import requests

    def _boom(*a, **k):
        raise requests.RequestException("network down")

    adapter = NaverFundamentalAdapter()
    monkeypatch.setattr(adapter._session, "get", _boom)

    assert adapter.get_one("005930", REF) is None


def test_get_many_skips_failures(monkeypatch) -> None:
    payload = {"totalInfos": [{"code": "per", "value": "10.0배"},
                              {"code": "bps", "value": "50,000원"}]}

    class _Resp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return payload

    def _get(url, *a, **k):
        if "BAD" in url:
            raise __import__("requests").RequestException("fail")
        return _Resp()

    adapter = NaverFundamentalAdapter()
    monkeypatch.setattr(adapter._session, "get", _get)

    rows = adapter.get_many(["005930", "BAD", "000660"], REF)

    assert len(rows) == 2
    assert {r.ticker for r in rows} == {"005930", "000660"}
