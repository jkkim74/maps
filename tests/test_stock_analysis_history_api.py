"""종목분석 이력 저장·조회·현재가 오버레이 API 테스트."""

from __future__ import annotations

import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import maps.common.models  # noqa: F401
from maps.common.db import Base
from maps.common.models import HistoricalOHLCV, StockAnalysisHistory


@pytest.fixture
def client():
    """독립 인메모리 DB를 사용하는 API 클라이언트."""
    from main import app
    from maps.api.deps import get_db

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(engine)

    def override_get_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    test_client = TestClient(app, raise_server_exceptions=True)
    test_client.session_factory = factory
    yield test_client
    app.dependency_overrides.pop(get_db, None)
    Base.metadata.drop_all(engine)
    engine.dispose()


def _analysis_result(price: float) -> dict:
    return {
        "종목코드": "005930",
        "종목명": "삼성전자",
        "시장": "KOSPI",
        "기술적분석": {"기준일": "2026-08-11", "현재가": price},
    }


def _trade_plan(recommendation: str = "BUY") -> dict:
    return {
        "recommendation": recommendation,
        "entries": [69_000, 67_000, 65_000] if recommendation == "BUY" else None,
        "target": 78_000 if recommendation == "BUY" else None,
        "stop": 61_000 if recommendation == "BUY" else None,
        "source": "AI" if recommendation == "BUY" else "MANUAL_REQUIRED",
    }


def _seed_history(
    client: TestClient,
    *,
    analyzed_price: float = 70_000,
    narrative: str = "분석",
) -> int:
    with client.session_factory() as db:
        row = StockAnalysisHistory(
            ticker="005930",
            name="삼성전자",
            market="KOSPI",
            ref_date=datetime.date(2026, 8, 11),
            snapshot=_analysis_result(analyzed_price),
            narrative=narrative,
            trade_plan=_trade_plan(),
            recommendation="BUY",
            analyzed_price=analyzed_price,
        )
        db.add(row)
        db.commit()
        return row.id


def _seed_ohlcv(client: TestClient, closes: list[float]) -> None:
    with client.session_factory() as db:
        for offset, close in enumerate(closes):
            day = datetime.date(2026, 8, 10 + offset)
            db.add(HistoricalOHLCV(
                ticker="005930",
                date=day,
                open=close,
                high=close,
                low=close,
                close=close,
                volume=1,
            ))
        db.commit()


class _QuoteBroker:
    def __init__(self, price: float | None = None, *, fail: bool = False) -> None:
        self.price = price
        self.fail = fail

    def get_current_prices(self, tickers: list[str]) -> dict[str, float]:
        if self.fail:
            raise RuntimeError("quote unavailable")
        return {tickers[0]: self.price} if self.price else {}


def test_save_appends_same_ticker_and_list_is_latest_first(client) -> None:
    first = _seed_history(client, analyzed_price=70_000, narrative="first")
    second = _seed_history(client, analyzed_price=71_000, narrative="second")

    response = client.get("/api/v1/stock-analysis/history")

    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body["items"]] == [second, first]
    assert body["total"] == 2
    assert body["items"][0]["created_at"].endswith("Z")
    assert "snapshot" not in body["items"][0]
    assert "narrative" not in body["items"][0]
    detail = client.get(f"/api/v1/stock-analysis/history/{first}").json()
    assert detail["snapshot"]["기술적분석"]["현재가"] == 70_000
    assert detail["narrative"] == "first"
    assert detail["trade_plan"]["entries"] == [69_000, 67_000, 65_000]
    assert client.get("/api/v1/stock-analysis/history/999999").status_code == 404


def test_save_service_maps_analysis_and_plan_without_deduplication(client) -> None:
    from maps.stock_analysis.history import save_analysis_history

    with client.session_factory() as db:
        first = save_analysis_history(
            db,
            result=_analysis_result(70_000),
            narrative="first",
            trade_plan=_trade_plan("WATCH"),
        )
        second = save_analysis_history(
            db,
            result=_analysis_result(71_000),
            narrative="second",
            trade_plan=_trade_plan("BUY"),
        )

        assert first.id != second.id
        assert first.snapshot["기술적분석"]["현재가"] == 70_000
        assert second.recommendation == "BUY"


def test_refresh_updates_only_quote_overlay(client, monkeypatch) -> None:
    history_id = _seed_history(client)
    before = client.get(f"/api/v1/stock-analysis/history/{history_id}").json()
    import maps.stock_analysis.history as service
    monkeypatch.setattr(service, "get_broker", lambda: _QuoteBroker(72_000))
    _seed_ohlcv(client, closes=[68_000, 70_000])

    refreshed = client.post(
        f"/api/v1/stock-analysis/history/{history_id}/refresh-price"
    )

    assert refreshed.status_code == 200
    overlay = refreshed.json()
    assert overlay["current_price"] == 72_000
    assert overlay["reference_close"] == 70_000
    assert overlay["plan_distances"]["entry_1"] == {
        "amount": -3_000.0,
        "pct": -4.17,
    }
    after = client.get(f"/api/v1/stock-analysis/history/{history_id}").json()
    assert after["snapshot"] == before["snapshot"]
    assert after["narrative"] == before["narrative"]
    assert after["trade_plan"] == before["trade_plan"]
    assert after["latest_price"] == 72_000


def test_refresh_falls_back_to_latest_ohlcv(client, monkeypatch) -> None:
    history_id = _seed_history(client)
    import maps.stock_analysis.history as service
    monkeypatch.setattr(service, "get_broker", lambda: _QuoteBroker(fail=True))
    _seed_ohlcv(client, closes=[68_000, 70_000])

    response = client.post(
        f"/api/v1/stock-analysis/history/{history_id}/refresh-price"
    )

    assert response.status_code == 200
    assert response.json()["current_price"] == 70_000
    assert response.json()["reference_close"] == 68_000
    assert response.json()["source"] == "historical_ohlcv"


def test_refresh_failure_preserves_existing_overlay(client, monkeypatch) -> None:
    history_id = _seed_history(client)
    import maps.stock_analysis.history as service
    monkeypatch.setattr(service, "get_broker", lambda: _QuoteBroker(fail=True))

    response = client.post(
        f"/api/v1/stock-analysis/history/{history_id}/refresh-price"
    )

    assert response.status_code == 503
    detail = client.get(f"/api/v1/stock-analysis/history/{history_id}").json()
    assert detail["latest_price"] is None
    assert detail["price_refreshed_at"] is None
