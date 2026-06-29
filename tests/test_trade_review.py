"""SCR-17 거래 리뷰 API 테스트."""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import maps.common.models  # noqa: F401
from maps.common.db import Base
from maps.common.models import HistoricalOHLCV, OrderLog, PortfolioSnapshot


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


def _seed_open_position(factory) -> None:
    """보유중(매수만 있고 매도 없음) 종목 1건을 시드한다."""
    today = dt.date.today()
    with factory() as s:
        s.add(PortfolioSnapshot(
            ref_date=today, source="broker", total_assets=100_000_000,
            cash=90_000_000, positions_value=10_000_000, holdings={"004490": 172},
        ))
        s.add(OrderLog(
            order_id="0000028034", strategy_id="strategy_trade", ticker="004490",
            side="buy", qty=172, order_price=49350, fill_price=48783, fill_qty=172,
            status="filled",
        ))
        s.add(HistoricalOHLCV(
            ticker="004490", date=today, open=50000, high=50000, low=50000,
            close=50000, volume=1000,
        ))
        s.commit()


def test_open_position_marked_open_with_unrealized_in_summary(client) -> None:
    _seed_open_position(client.session_factory)
    data = client.get("/api/v1/trade-review").json()

    trade = next(t for t in data["trades"] if t["ticker"] == "004490")
    assert trade["status"] == "open"
    assert trade["note"] == "보유 중"
    # 데이터 계약: exit_price/pnl은 미실현 값으로 채워진다(표시 숨김은 프론트 책임).
    assert trade["exit_price"] == 50000
    assert trade["pnl"] == (50000 - 48783) * 172  # 미실현 손익
    # 미실현 손익은 상단 KPI에 유지된다.
    assert data["summary"]["unrealized_pnl"] != 0
    assert data["summary"]["open_trades"] == 1
    assert data["summary"]["closed_trades"] == 0


def test_closed_position_keeps_exit_values(client) -> None:
    today = dt.date.today()
    with client.session_factory() as s:
        # 보유 없음(holdings 비어있음) → 매도 체결 기록이 있으면 closed
        s.add(PortfolioSnapshot(
            ref_date=today, source="broker", total_assets=100_000_000,
            cash=100_000_000, positions_value=0, holdings={},
        ))
        s.add(OrderLog(
            order_id="buy-1", strategy_id="strategy_trade", ticker="005930",
            side="buy", qty=10, order_price=70000, fill_price=70000, fill_qty=10,
            status="filled",
        ))
        s.add(OrderLog(
            order_id="sell-1", strategy_id="strategy_trade", ticker="005930",
            side="sell", qty=10, order_price=75000, fill_price=75000, fill_qty=10,
            status="filled",
        ))
        s.commit()

    data = client.get("/api/v1/trade-review").json()
    trade = next(t for t in data["trades"] if t["ticker"] == "005930")
    assert trade["status"] == "closed"
    assert trade["exit_price"] == 75000
    assert trade["pnl"] == (75000 - 70000) * 10
