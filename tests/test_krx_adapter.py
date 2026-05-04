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
