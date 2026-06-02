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
def ctx():
    from main import app
    from maps.api.deps import get_db

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app, raise_server_exceptions=True)
    yield client, factory

    app.dependency_overrides.pop(get_db, None)
    Base.metadata.drop_all(engine)
    engine.dispose()


def _add_ohlcv_series(db, ticker: str, bars: int) -> None:
    start = dt.date(2026, 1, 1)
    for offset in range(bars):
        close = 100.0 + offset
        db.add(HistoricalOHLCV(
            ticker=ticker,
            date=start + dt.timedelta(days=offset),
            open=close - 1,
            high=close + 1,
            low=close - 2,
            close=close,
            volume=1000 + offset,
            adj_close=close,
        ))


def test_trend_strength_endpoint_scores_batch_history(ctx) -> None:
    client, factory = ctx
    db = factory()
    try:
        _add_ohlcv_series(db, "000001", 65)
        _add_ohlcv_series(db, "000002", 10)
        db.commit()
    finally:
        db.close()

    response = client.get("/api/v1/trend-strength?min_bars=60")

    assert response.status_code == 200
    data = response.json()
    assert data["universe_count"] == 2
    assert data["missing_count"] == 1
    assert sum(bucket["count"] for bucket in data["buckets"]) == 1
