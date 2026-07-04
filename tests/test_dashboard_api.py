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
from maps.common.models import KillSwitchLog, PortfolioSnapshot
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


def test_dashboard_alerts_exclude_kill_switch_older_than_24h(db) -> None:
    now = dt.datetime.now(dt.timezone.utc)
    db.add(KillSwitchLog(
        strategy_id="donchian_v2",
        event_type="trigger",
        reason="consecutive_failure",
        created_at=now - dt.timedelta(days=3),
    ))
    db.add(KillSwitchLog(
        strategy_id="pullback_v3",
        event_type="trigger",
        reason="daily_loss",
        created_at=now - dt.timedelta(hours=1),
    ))
    db.commit()

    alerts = dashboard._dashboard_alerts(db, None)

    messages = [a.message for a in alerts]
    assert any("pullback_v3" in m for m in messages)
    assert not any("donchian_v2" in m for m in messages)


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


# ---------------------------------------------------------------------------
# M-2: /api/v1/dashboard/pnl/daily 엔드포인트
# ---------------------------------------------------------------------------

def test_daily_pnl_returns_correct_returns(ctx) -> None:
    """PortfolioSnapshot 3개로부터 일별 손익률과 누적 수익률을 올바르게 계산한다."""
    client, monkeypatch = ctx

    class FakeBroker:
        def get_account_balance(self):
            return AccountBalance(cash=110_000_000, positions_value=0)

    monkeypatch.setattr(dashboard, "get_broker", lambda: FakeBroker())
    # cutoff = 2026-05-10 - 30일 = 2026-04-10 → 5월 스냅샷이 포함됨
    monkeypatch.setattr(dashboard, "_today", lambda: dt.date(2026, 5, 10))

    from main import app
    from maps.api.deps import get_db

    db = next(app.dependency_overrides[get_db]())
    try:
        db.add_all([
            PortfolioSnapshot(ref_date=dt.date(2026, 5, 1), source="broker", total_assets=100_000_000),
            PortfolioSnapshot(ref_date=dt.date(2026, 5, 2), source="broker", total_assets=102_000_000),
            PortfolioSnapshot(ref_date=dt.date(2026, 5, 3), source="broker", total_assets=99_960_000),
        ])
        db.commit()
    finally:
        db.close()

    response = client.get("/api/v1/dashboard/pnl/daily?days=30")

    assert response.status_code == 200
    data = response.json()
    assert data["days"] == 30
    assert len(data["items"]) == 3

    # 첫 번째 항목: 전일 없으므로 pnl_pct=0
    assert data["items"][0]["pnl_pct"] == pytest.approx(0.0)
    # 두 번째: +2% 상승
    assert data["items"][1]["pnl_pct"] == pytest.approx(0.02, abs=1e-5)
    assert data["items"][1]["pnl_amount"] == pytest.approx(2_000_000, rel=1e-3)
    # 세 번째: -2% 하락
    assert data["items"][2]["pnl_pct"] == pytest.approx(-0.02, abs=1e-5)

    # 누적: 99,960,000 / 100,000,000 - 1 = -0.0004
    assert data["cumulative_pct"] == pytest.approx(-0.0004, abs=1e-5)


def test_daily_pnl_empty_when_no_snapshots(ctx) -> None:
    """스냅샷이 없으면 items 가 비어 있고 cumulative_pct=0 을 반환한다."""
    client, _ = ctx

    response = client.get("/api/v1/dashboard/pnl/daily?days=30")

    assert response.status_code == 200
    data = response.json()
    assert data["items"] == []
    assert data["cumulative_pct"] == 0.0


def test_daily_pnl_days_parameter_filters_older_snapshots(ctx) -> None:
    """days 파라미터로 오래된 스냅샷이 제외된다."""
    client, monkeypatch = ctx

    class FakeBroker:
        def get_account_balance(self):
            return AccountBalance(cash=100_000_000, positions_value=0)

    monkeypatch.setattr(dashboard, "get_broker", lambda: FakeBroker())
    monkeypatch.setattr(dashboard, "_today", lambda: dt.date(2026, 5, 10))

    from main import app
    from maps.api.deps import get_db

    db = next(app.dependency_overrides[get_db]())
    try:
        db.add_all([
            # 11일 전 — days=10 조회 시 제외되어야 함
            PortfolioSnapshot(ref_date=dt.date(2026, 4, 29), source="broker", total_assets=100_000_000),
            # 5일 전 — 포함
            PortfolioSnapshot(ref_date=dt.date(2026, 5, 5), source="broker", total_assets=105_000_000),
        ])
        db.commit()
    finally:
        db.close()

    response = client.get("/api/v1/dashboard/pnl/daily?days=10")

    assert response.status_code == 200
    data = response.json()
    # 2026-04-29 는 days=10 기준 cutoff(2026-04-30) 이전이므로 제외
    assert len(data["items"]) == 1
    assert data["items"][0]["date"] == "2026-05-05"
