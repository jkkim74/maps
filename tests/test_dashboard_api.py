from __future__ import annotations

import pytest
import datetime as dt
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import maps.common.models  # noqa: F401
from maps.api import dashboard
from maps.common.db import Base
from maps.common.exceptions import BrokerAdapterError
from maps.common.models import PortfolioSnapshot
from maps.execution.broker_adapter import AccountBalance


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

    def override_get_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app, raise_server_exceptions=True)
    yield client, monkeypatch

    app.dependency_overrides.pop(get_db, None)
    Base.metadata.drop_all(engine)
    engine.dispose()


def test_dashboard_total_assets_uses_broker_balance(ctx) -> None:
    client, monkeypatch = ctx

    class FakeBroker:
        def get_account_balance(self):
            return AccountBalance(cash=100_000_000, positions_value=0)

    monkeypatch.setattr(dashboard, "get_broker", lambda: FakeBroker())

    response = client.get("/api/v1/dashboard")

    assert response.status_code == 200
    assert response.json()["total_assets"] == 100_000_000
    assert response.json()["active_strategies"] > 0


def test_dashboard_keeps_rendering_when_broker_balance_fails(ctx) -> None:
    client, monkeypatch = ctx

    class BrokenBroker:
        def get_account_balance(self):
            raise BrokerAdapterError("paper account unavailable")

    monkeypatch.setattr(dashboard, "get_broker", lambda: BrokenBroker())

    response = client.get("/api/v1/dashboard")

    assert response.status_code == 200
    data = response.json()
    assert data["total_assets"] == 0
    assert data["alerts"][0]["level"] == "WARN"
    assert "Broker account balance unavailable" in data["alerts"][0]["message"]


def test_dashboard_calculates_portfolio_metrics_from_snapshots(ctx) -> None:
    client, monkeypatch = ctx
    today = dt.date(2026, 5, 5)

    class FakeBroker:
        def get_account_balance(self):
            return AccountBalance(cash=110_000_000, positions_value=0)

    monkeypatch.setattr(dashboard, "_today", lambda: today)
    monkeypatch.setattr(dashboard, "get_broker", lambda: FakeBroker())

    from main import app
    from maps.api.deps import get_db

    db = next(app.dependency_overrides[get_db]())
    try:
        db.add_all([
            PortfolioSnapshot(ref_date=dt.date(2026, 1, 1), source="broker", total_assets=100_000_000),
            PortfolioSnapshot(ref_date=dt.date(2026, 3, 1), source="broker", total_assets=120_000_000),
            PortfolioSnapshot(ref_date=dt.date(2026, 4, 1), source="broker", total_assets=108_000_000),
        ])
        db.commit()
    finally:
        db.close()

    response = client.get("/api/v1/dashboard")

    assert response.status_code == 200
    data = response.json()
    assert data["total_assets"] == 110_000_000
    assert data["total_assets_mom_pct"] > 0
    assert data["ytd_cagr"] > 0
    assert data["current_mdd"] == pytest.approx(-0.1)
    assert data["sharpe_1y"] != 0
