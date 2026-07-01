"""추이 차트: 모바일 portfolio-history 엔드포인트 테스트."""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import maps.common.models  # noqa: F401
from maps.common.db import Base


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


def _seed_snapshots(client, rows: list[tuple[dt.date, float]], source: str = "broker") -> None:
    """(ref_date, total_assets) 목록으로 PortfolioSnapshot을 시드한다."""
    from maps.common.models import PortfolioSnapshot

    with client.session_factory() as s:
        for ref_date, total in rows:
            s.add(PortfolioSnapshot(
                ref_date=ref_date, source=source, total_assets=total,
                cash=total, positions_value=0.0,
            ))
        s.commit()


def test_empty_returns_no_points(client) -> None:
    r = client.get("/api/v1/mobile/portfolio-history")
    assert r.status_code == 200
    body = r.json()
    assert body["points"] == []
    assert body["cumulative_pct"] == 0.0
    assert body["days"] == 30


def test_series_ordered_with_pnl_and_cumulative(client) -> None:
    today = dt.date.today()
    _seed_snapshots(client, [
        (today - dt.timedelta(days=2), 1_000_000.0),
        (today - dt.timedelta(days=1), 1_100_000.0),
        (today, 1_045_000.0),
    ])
    body = client.get("/api/v1/mobile/portfolio-history?days=30").json()
    points = body["points"]
    assert len(points) == 3
    # 날짜 오름차순
    assert points[0]["date"] < points[1]["date"] < points[2]["date"]
    assert points[0]["total_value"] == 1_000_000.0
    # 첫날 pnl_pct = 0, 둘째날 +10%, 셋째날 -5%
    assert points[0]["pnl_pct"] == 0.0
    assert round(points[1]["pnl_pct"], 4) == 0.1
    assert round(points[2]["pnl_pct"], 4) == -0.05
    # 누적 = 1,045,000 / 1,000,000 - 1 = +4.5%
    assert round(body["cumulative_pct"], 4) == 0.045


def test_ignores_non_broker_source(client) -> None:
    today = dt.date.today()
    _seed_snapshots(client, [(today, 500_000.0)], source="mock")
    body = client.get("/api/v1/mobile/portfolio-history").json()
    assert body["points"] == []


def test_days_out_of_range_rejected(client) -> None:
    assert client.get("/api/v1/mobile/portfolio-history?days=0").status_code == 422
    assert client.get("/api/v1/mobile/portfolio-history?days=999").status_code == 422
