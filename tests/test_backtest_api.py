"""SCR-07 백테스트 콘솔 API — 실행 설정 패널 실측값 테스트."""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import maps.common.models  # noqa: F401
from maps.common.db import Base
from maps.common.models import HistoricalOHLCV


@pytest.fixture
def client():
    from main import app
    from maps.api.deps import get_db

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    db = factory()
    for day, close in ((dt.date(2019, 1, 2), 100.0), (dt.date(2026, 7, 31), 120.0)):
        db.add(HistoricalOHLCV(
            ticker="005930",
            date=day,
            open=close,
            high=close,
            low=close,
            close=close,
            volume=1_000,
        ))
    db.commit()
    db.close()

    def override_get_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app, raise_server_exceptions=True)

    app.dependency_overrides.pop(get_db, None)
    Base.metadata.drop_all(engine)
    engine.dispose()


def test_backtest_panel_reflects_actual_db_range_and_cost(client) -> None:
    """실행 설정 패널 값은 하드코딩이 아니라 DB 실측·비용 상수여야 한다."""
    response = client.get("/api/v1/backtest")

    assert response.status_code == 200
    body = response.json()
    assert body["data_start"] == "2019-01-02"
    assert body["data_end"] == "2026-07-31"
    assert body["max_tickers"] == 30
    assert "0.18%" in body["cost_summary"]  # 매도 거래세
    assert "0.015%" in body["cost_summary"]  # 편도 수수료
