from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import maps.common.models  # noqa: F401
from maps.api import risk
from maps.common.db import Base
from maps.execution.broker_adapter import AccountBalance, Position


@pytest.fixture
def ctx(monkeypatch):
    from main import app
    from maps.api.deps import get_db

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    class FakeBroker:
        def get_account_balance(self):
            return AccountBalance(cash=900_000, positions_value=100_000)

        def _fetch_positions_and_balance(self):
            return {
                "005930": Position(
                    "005930",
                    2,
                    50_000,
                    name="삼성전자",
                    current_price=52_000,
                    evaluation_value=104_000,
                )
            }, self.get_account_balance()

    monkeypatch.setattr(risk, "get_broker", lambda: FakeBroker())

    def override_get_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app, raise_server_exceptions=True)
    yield client

    app.dependency_overrides.pop(get_db, None)
    Base.metadata.drop_all(engine)
    engine.dispose()


def test_risk_returns_default_strategy_gauges_and_broker_holdings(ctx) -> None:
    response = ctx.get("/api/v1/risk")

    assert response.status_code == 200
    data = response.json()
    ids = {item["strategy_id"] for item in data["gauges"]}
    assert "pullback_v3" in ids
    assert data["holdings"] == [
        {
            "ticker": "005930",
            "name": "삼성전자",
            "strategy_id": "broker",
            "entry_price": 50000.0,
            "current_price": 52000.0,
            "pnl_pct": 0.040000000000000036,
            "exposure_pct": 0.104,
            "stop_price": None,
        }
    ]
    assert data["max_exposure_pct"] == 0.104
    assert data["position_count"] == 1
