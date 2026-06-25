"""SCR-19 분석 워치리스트 API 테스트."""

from __future__ import annotations

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
    yield test_client

    app.dependency_overrides.pop(get_db, None)
    Base.metadata.drop_all(engine)
    engine.dispose()


def _sample(**overrides):
    base = {
        "ticker": "005930",
        "name": "삼성전자",
        "market": "KOSPI",
        "source": "manual",
        "buy_price": 70000,
        "target_price": 80000,
        "stop_price": 66000,
    }
    base.update(overrides)
    return base


def test_list_empty(client) -> None:
    r = client.get("/api/v1/analysis-picks")
    assert r.status_code == 200
    assert r.json() == {"total": 0, "picks": []}


def test_create_single_and_rr_ratio(client) -> None:
    r = client.post("/api/v1/analysis-picks", json={"picks": [_sample()]})
    assert r.status_code == 200
    item = r.json()["picks"][0]
    assert item["ticker"] == "005930"
    assert item["state"] == "WATCH"
    assert item["strategy_trade_enabled"] is False
    # R:R = (80000-70000)/(70000-66000) = 2.5
    assert item["rr_ratio"] == 2.5


def test_create_bulk(client) -> None:
    payload = {"picks": [
        _sample(ticker="005930", name="삼성전자"),
        _sample(ticker="000660", name="SK하이닉스", buy_price=180000, target_price=210000, stop_price=168000),
    ]}
    r = client.post("/api/v1/analysis-picks", json=payload)
    assert r.status_code == 200
    assert r.json()["total"] == 2
    assert client.get("/api/v1/analysis-picks").json()["total"] == 2


def test_create_empty_400(client) -> None:
    r = client.post("/api/v1/analysis-picks", json={"picks": []})
    assert r.status_code == 400


def test_rr_ratio_none_without_prices(client) -> None:
    r = client.post("/api/v1/analysis-picks", json={"picks": [
        {"ticker": "035720", "name": "카카오"},
    ]})
    assert r.json()["picks"][0]["rr_ratio"] is None


def test_filter_by_source(client) -> None:
    client.post("/api/v1/analysis-picks", json={"picks": [_sample(source="manual")]})
    client.post("/api/v1/analysis-picks", json={"picks": [_sample(ticker="000660", source="analyze")]})
    assert client.get("/api/v1/analysis-picks?source=analyze").json()["total"] == 1
    assert client.get("/api/v1/analysis-picks?source=manual").json()["total"] == 1


def test_patch_toggle_and_state(client) -> None:
    pid = client.post("/api/v1/analysis-picks", json={"picks": [_sample()]}).json()["picks"][0]["id"]
    r = client.patch(f"/api/v1/analysis-picks/{pid}", json={"strategy_trade_enabled": True, "state": "ARMED"})
    assert r.status_code == 200
    assert r.json()["strategy_trade_enabled"] is True
    assert r.json()["state"] == "ARMED"


def test_patch_invalid_state_400(client) -> None:
    pid = client.post("/api/v1/analysis-picks", json={"picks": [_sample()]}).json()["picks"][0]["id"]
    r = client.patch(f"/api/v1/analysis-picks/{pid}", json={"state": "BOGUS"})
    assert r.status_code == 400


def test_patch_missing_404(client) -> None:
    assert client.patch("/api/v1/analysis-picks/9999", json={"state": "WATCH"}).status_code == 404


def test_delete_and_missing_404(client) -> None:
    pid = client.post("/api/v1/analysis-picks", json={"picks": [_sample()]}).json()["picks"][0]["id"]
    assert client.delete(f"/api/v1/analysis-picks/{pid}").status_code == 200
    assert client.get("/api/v1/analysis-picks").json()["total"] == 0
    assert client.delete(f"/api/v1/analysis-picks/{pid}").status_code == 404


def test_state_filter_after_patch(client) -> None:
    pid = client.post("/api/v1/analysis-picks", json={"picks": [_sample()]}).json()["picks"][0]["id"]
    client.patch(f"/api/v1/analysis-picks/{pid}", json={"state": "BOUGHT"})
    assert client.get("/api/v1/analysis-picks?state=BOUGHT").json()["total"] == 1
    assert client.get("/api/v1/analysis-picks?state=WATCH").json()["total"] == 0


def _new_pick(client, **overrides):
    return client.post("/api/v1/analysis-picks", json={"picks": [_sample(**overrides)]}).json()["picks"][0]["id"]


def test_arm_success(client) -> None:
    pid = _new_pick(client)
    r = client.post(f"/api/v1/analysis-picks/{pid}/arm")
    assert r.status_code == 200
    assert r.json()["state"] == "ARMED"
    assert r.json()["strategy_trade_enabled"] is True


def test_arm_requires_all_prices(client) -> None:
    pid = _new_pick(client, target_price=None)
    assert client.post(f"/api/v1/analysis-picks/{pid}/arm").status_code == 400


def test_arm_rejects_bad_price_order(client) -> None:
    # stop > buy → 정합성 위반
    pid = _new_pick(client, buy_price=70000, stop_price=72000, target_price=80000)
    assert client.post(f"/api/v1/analysis-picks/{pid}/arm").status_code == 400


def test_arm_conflict_when_not_watch(client) -> None:
    pid = _new_pick(client)
    client.post(f"/api/v1/analysis-picks/{pid}/arm")
    assert client.post(f"/api/v1/analysis-picks/{pid}/arm").status_code == 409


def test_disarm_from_armed(client) -> None:
    pid = _new_pick(client)
    client.post(f"/api/v1/analysis-picks/{pid}/arm")
    r = client.post(f"/api/v1/analysis-picks/{pid}/disarm")
    assert r.status_code == 200
    assert r.json()["state"] == "WATCH"
    assert r.json()["strategy_trade_enabled"] is False


def test_disarm_rejected_when_bought(client) -> None:
    pid = _new_pick(client)
    client.patch(f"/api/v1/analysis-picks/{pid}", json={"state": "BOUGHT"})
    assert client.post(f"/api/v1/analysis-picks/{pid}/disarm").status_code == 409


def test_arm_missing_404(client) -> None:
    assert client.post("/api/v1/analysis-picks/9999/arm").status_code == 404
