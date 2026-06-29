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
    test_client.session_factory = factory   # 일부 테스트에서 직접 DB 시드용
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


def test_list_current_price_from_latest_close(client) -> None:
    import datetime as dt

    from maps.common.models import HistoricalOHLCV

    client.post("/api/v1/analysis-picks", json={"picks": [_sample(ticker="005930")]})
    with client.session_factory() as s:
        for d, close in [(dt.date(2026, 6, 24), 70000), (dt.date(2026, 6, 25), 71500)]:
            s.add(HistoricalOHLCV(
                ticker="005930", date=d, open=close, high=close, low=close,
                close=close, volume=1000,
            ))
        s.commit()
    item = client.get("/api/v1/analysis-picks").json()["picks"][0]
    assert item["current_price"] == 71500  # 티커별 최신 date(6/25) 종가


def test_list_current_price_none_without_ohlcv(client) -> None:
    client.post("/api/v1/analysis-picks", json={"picks": [_sample(ticker="005930")]})
    item = client.get("/api/v1/analysis-picks").json()["picks"][0]
    assert item["current_price"] is None


def test_list_current_price_prefers_broker_live(client, monkeypatch) -> None:
    """보유 종목은 브로커 라이브 현재가가 일봉 종가를 덮어쓴다(리스크 모니터와 동일 소스)."""
    import datetime as dt

    from maps.common.models import HistoricalOHLCV
    from maps.execution.broker_adapter import Order, OrderSide, OrderType
    from maps.execution.mock_broker import MockBroker

    client.post("/api/v1/analysis-picks", json={"picks": [_sample(ticker="005930")]})
    with client.session_factory() as s:
        s.add(HistoricalOHLCV(
            ticker="005930", date=dt.date(2026, 6, 25), open=71500, high=71500,
            low=71500, close=71500, volume=1000,
        ))
        s.commit()

    broker = MockBroker(price_feed={"005930": 72800})
    broker.place_order(Order(  # 보유 포지션 생성
        strategy_id="t", ticker="005930", side=OrderSide.BUY,
        order_type=OrderType.LIMIT, quantity=10, limit_price=70000,
    ))
    monkeypatch.setattr("maps.api.analysis_picks.get_broker", lambda *a, **k: broker)

    item = client.get("/api/v1/analysis-picks").json()["picks"][0]
    assert item["current_price"] == 72800  # 일봉 종가(71500)가 아니라 라이브 시세


def _seed_bought_pick_with_order(client, *, fill_price, order_price, status="filled"):
    """BOUGHT 픽 + 연결된 진입 OrderLog를 시드하고 pick_id를 반환한다."""
    from maps.common.models import AnalysisPick, OrderLog

    pid = client.post("/api/v1/analysis-picks", json={"picks": [_sample(ticker="005930")]}).json()["picks"][0]["id"]
    with client.session_factory() as s:
        pick = s.get(AnalysisPick, pid)
        pick.state = "BOUGHT"
        pick.entry_order_id = "ord-005930-1"
        s.add(OrderLog(
            order_id="ord-005930-1", strategy_id="strategy_trade", ticker="005930",
            side="buy", qty=10, order_price=order_price, fill_price=fill_price,
            fill_qty=10, status=status,
        ))
        s.commit()
    return pid


def test_fill_price_from_order_log(client) -> None:
    _seed_bought_pick_with_order(client, fill_price=70150, order_price=70000)
    item = client.get("/api/v1/analysis-picks").json()["picks"][0]
    assert item["fill_price"] == 70150  # 실제 체결가
    assert item["buy_price"] == 70000   # 계획 매수가는 유지


def test_fill_price_falls_back_to_order_price(client) -> None:
    _seed_bought_pick_with_order(client, fill_price=None, order_price=70000)
    item = client.get("/api/v1/analysis-picks").json()["picks"][0]
    assert item["fill_price"] == 70000  # fill_price 없으면 order_price 폴백


def test_fill_price_none_without_entry_order(client) -> None:
    client.post("/api/v1/analysis-picks", json={"picks": [_sample(ticker="005930")]})
    item = client.get("/api/v1/analysis-picks").json()["picks"][0]
    assert item["fill_price"] is None  # 미체결(WATCH, 진입주문 없음)


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


def test_patch_updates_prices_and_rr(client) -> None:
    pid = client.post("/api/v1/analysis-picks", json={"picks": [_sample()]}).json()["picks"][0]["id"]
    r = client.patch(f"/api/v1/analysis-picks/{pid}", json={"target_price": 90000})
    assert r.status_code == 200
    assert r.json()["target_price"] == 90000
    # rr 재계산: (90000-70000)/(70000-66000)=5.0
    assert r.json()["rr_ratio"] == 5.0


def test_patch_ignores_state_field(client) -> None:
    # state/strategy_trade_enabled는 PATCH 대상이 아님 (arm/disarm 전용) — 무시되고 WATCH 유지
    pid = client.post("/api/v1/analysis-picks", json={"picks": [_sample()]}).json()["picks"][0]["id"]
    r = client.patch(f"/api/v1/analysis-picks/{pid}", json={"state": "ARMED", "strategy_trade_enabled": True})
    assert r.status_code == 200
    assert r.json()["state"] == "WATCH"
    assert r.json()["strategy_trade_enabled"] is False


def test_patch_price_blocked_when_armed(client) -> None:
    pid = _new_pick(client)
    client.post(f"/api/v1/analysis-picks/{pid}/arm")
    assert client.patch(f"/api/v1/analysis-picks/{pid}", json={"buy_price": 71000}).status_code == 409


def test_patch_missing_404(client) -> None:
    assert client.patch("/api/v1/analysis-picks/9999", json={"rationale": "x"}).status_code == 404


def test_delete_and_missing_404(client) -> None:
    pid = client.post("/api/v1/analysis-picks", json={"picks": [_sample()]}).json()["picks"][0]["id"]
    assert client.delete(f"/api/v1/analysis-picks/{pid}").status_code == 200
    assert client.get("/api/v1/analysis-picks").json()["total"] == 0
    assert client.delete(f"/api/v1/analysis-picks/{pid}").status_code == 404


def test_state_filter_via_arm(client) -> None:
    pid = _new_pick(client)
    client.post(f"/api/v1/analysis-picks/{pid}/arm")
    assert client.get("/api/v1/analysis-picks?state=ARMED").json()["total"] == 1
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
    from maps.common.models import AnalysisPick
    pid = _new_pick(client)
    with client.session_factory() as s:
        s.get(AnalysisPick, pid).state = "BOUGHT"
        s.commit()
    assert client.post(f"/api/v1/analysis-picks/{pid}/disarm").status_code == 409


def test_arm_missing_404(client) -> None:
    assert client.post("/api/v1/analysis-picks/9999/arm").status_code == 404


def test_disarm_rejected_when_entry_cancel_unconfirmed(client) -> None:
    # 진입 주문이 살아있는데 mock 브로커 cancel_order가 False면 disarm 거부(409)
    from maps.common.models import AnalysisPick
    pid = _new_pick(client)
    client.post(f"/api/v1/analysis-picks/{pid}/arm")
    with client.session_factory() as s:
        s.get(AnalysisPick, pid).entry_order_id = "live-order-xyz"
        s.commit()
    assert client.post(f"/api/v1/analysis-picks/{pid}/disarm").status_code == 409
