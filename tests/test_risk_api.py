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
from maps.common.models import OrderLog
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
    db = factory()
    db.add(OrderLog(
        order_id="filled-005930",
        strategy_id="pullback_v3",
        ticker="005930",
        side="buy",
        qty=2,
        order_price=50_000,
        fill_price=50_000,
        fill_qty=2,
        status="filled",
    ))
    db.commit()
    db.close()

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
            "strategy_id": "pullback_v3",
            "entry_price": 50000.0,
            "current_price": 52000.0,
            "pnl_pct": 0.040000000000000036,
            "exposure_pct": 0.104,
            "stop_price": 47500.0,
        }
    ]
    assert data["max_exposure_pct"] == 0.104
    assert data["position_count"] == 1


def test_broker_holdings_infers_strategy_from_matching_buy_order(db, monkeypatch) -> None:
    db.add(OrderLog(
        order_id="expired-005930",
        strategy_id="donchian_v2",
        ticker="005930",
        side="buy",
        qty=28,
        order_price=352_500,
        fill_price=None,
        fill_qty=0,
        status="expired",
    ))
    db.commit()

    class FakeBroker:
        def get_account_balance(self):
            return AccountBalance(cash=900_000, positions_value=10_094_000)

        def _fetch_positions_and_balance(self):
            return {
                "005930": Position(
                    "005930",
                    28,
                    352_500,
                    name="삼성전자",
                    current_price=360_500,
                    evaluation_value=10_094_000,
                )
            }, self.get_account_balance()

    monkeypatch.setattr(risk, "get_broker", lambda: FakeBroker())

    holdings, _max_exposure, count = risk._broker_holdings(db)

    assert count == 1
    assert holdings[0].strategy_id == "donchian_v2"
    assert holdings[0].stop_price == 317_250.0


def test_broker_holdings_includes_stop_triggered_position_when_still_held(db, monkeypatch) -> None:
    db.add(OrderLog(
        order_id="filled-009150",
        strategy_id="donchian_v2",
        ticker="009150",
        side="buy",
        qty=4,
        order_price=2_149_000,
        fill_price=2_067_000,
        fill_qty=4,
        status="filled",
    ))
    db.commit()

    class FakeBroker:
        def get_account_balance(self):
            return AccountBalance(cash=900_000, positions_value=7_252_000)

        def _fetch_positions_and_balance(self):
            return {
                "009150": Position(
                    "009150",
                    4,
                    2_067_000,
                    name="삼성전기",
                    current_price=1_813_000,
                    evaluation_value=7_252_000,
                )
            }, self.get_account_balance()

    monkeypatch.setattr(risk, "get_broker", lambda: FakeBroker())

    holdings, max_exposure, count = risk._broker_holdings(db)

    assert count == 1
    assert max_exposure == 7_252_000 / 8_152_000
    assert holdings[0].ticker == "009150"
    assert holdings[0].strategy_id == "donchian_v2"
    assert holdings[0].stop_price == 1_860_300.0
