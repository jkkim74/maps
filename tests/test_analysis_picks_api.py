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
    body = r.json()
    assert body["total"] == 0
    assert body["picks"] == []
    assert body["stale_count"] == 0
    assert body["expected_ref_date"]   # 신선도 기준일은 목록이 비어도 내려간다


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


def test_list_current_price_live_quote_for_unheld(client, monkeypatch) -> None:
    """미보유(WATCH) 종목은 실시간 시세 조회가 일봉 종가를 덮어쓴다."""
    import datetime as dt

    from maps.common.models import HistoricalOHLCV
    from maps.execution.mock_broker import MockBroker

    client.post("/api/v1/analysis-picks", json={"picks": [_sample(ticker="005930")]})
    with client.session_factory() as s:
        s.add(HistoricalOHLCV(
            ticker="005930", date=dt.date(2026, 6, 25), open=71500, high=71500,
            low=71500, close=71500, volume=1000,
        ))
        s.commit()

    # 보유는 없지만 브로커 시세 조회가 실시간 현재가를 준다.
    broker = MockBroker(price_feed={"005930": 73400})
    monkeypatch.setattr("maps.api.analysis_picks.get_broker", lambda *a, **k: broker)

    item = client.get("/api/v1/analysis-picks").json()["picks"][0]
    assert item["current_price"] == 73400  # 일봉 종가(71500)가 아니라 실시간 시세


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
    # 손익비는 체결가(70150) 기준: (80000-70150)/(70150-66000) = 9850/4150 ≈ 2.37
    assert item["rr_ratio"] == 2.37


def test_fill_price_dropped_decimals(client) -> None:
    _seed_bought_pick_with_order(client, fill_price=70150.7, order_price=70000)
    item = client.get("/api/v1/analysis-picks").json()["picks"][0]
    assert item["fill_price"] == 70151  # 소수점 반올림 → 정수


def test_rr_ratio_uses_planned_buy_when_unfilled(client) -> None:
    # 미체결(WATCH) 픽은 계획 매수가(70000) 기준: (80000-70000)/(70000-66000)=2.5
    client.post("/api/v1/analysis-picks", json={"picks": [_sample(ticker="005930")]})
    item = client.get("/api/v1/analysis-picks").json()["picks"][0]
    assert item["fill_price"] is None
    assert item["rr_ratio"] == 2.5


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


def test_closed_hidden_by_default_shown_when_requested(client) -> None:
    # 익절/손절로 CLOSED된 픽은 기본 목록에서 빠지고 ?state=CLOSED로만 조회된다(완료 목록 분리).
    from maps.common.models import AnalysisPick

    pid = _new_pick(client)
    with client.session_factory() as s:
        pick = s.get(AnalysisPick, pid)
        pick.state = "CLOSED"
        pick.exit_reason = "take_profit"
        s.commit()
    assert client.get("/api/v1/analysis-picks").json()["total"] == 0
    closed = client.get("/api/v1/analysis-picks?state=CLOSED").json()["picks"]
    assert len(closed) == 1
    assert closed[0]["exit_reason"] == "take_profit"


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


def _seed_entry_order(client, pid: int, *, order_id: str, status: str, fill_qty: int) -> None:
    """픽에 진입 주문을 연결하고 order_log 행을 심는다."""
    from maps.common.models import AnalysisPick, OrderLog
    with client.session_factory() as s:
        s.get(AnalysisPick, pid).entry_order_id = order_id
        s.add(OrderLog(
            order_id=order_id, strategy_id="strategy_trade", ticker="005930",
            side="BUY", qty=1000, order_price=70000.0, fill_price=69000.0,
            fill_qty=fill_qty, status=status, broker="mock",
        ))
        s.commit()


def test_disarm_rejected_when_entry_partially_filled(client) -> None:
    """부분 체결분이 있으면 해제를 거부하고 BOUGHT 로 올린다.

    잔량 취소가 성공해도 이미 체결된 주식은 남는다. 해제해서 entry_order_id 를
    비우면 브래킷도 손절도 관리하지 않는 고아 포지션이 된다(2026-07-30 실제 발생).
    """
    from maps.common.models import AnalysisPick
    pid = _new_pick(client)
    client.post(f"/api/v1/analysis-picks/{pid}/arm")
    _seed_entry_order(client, pid, order_id="ord-partial", status="partially_filled", fill_qty=21)

    r = client.post(f"/api/v1/analysis-picks/{pid}/disarm")
    assert r.status_code == 409
    assert "21주" in r.json()["detail"]

    with client.session_factory() as s:
        pick = s.get(AnalysisPick, pid)
        # 고아 방지: 체결분을 브래킷이 계속 추적해야 한다.
        assert pick.state == "BOUGHT"
        assert pick.entry_order_id == "ord-partial"


def test_disarm_rejected_when_entry_filled_without_fill_qty(client) -> None:
    """fill_qty 가 아직 동기화되지 않았어도 체결 상태면 보유로 간주한다."""
    from maps.common.models import AnalysisPick
    pid = _new_pick(client)
    client.post(f"/api/v1/analysis-picks/{pid}/arm")
    _seed_entry_order(client, pid, order_id="ord-filled", status="filled", fill_qty=0)

    r = client.post(f"/api/v1/analysis-picks/{pid}/disarm")
    assert r.status_code == 409
    assert "수량 미상" in r.json()["detail"]
    with client.session_factory() as s:
        assert s.get(AnalysisPick, pid).state == "BOUGHT"


def test_disarm_rejected_when_partially_filled_and_cancel_succeeds(client, monkeypatch) -> None:
    """2026-07-30 사고 재현 — 잔량 취소가 **성공**해도 체결분은 남는다.

    실제 KIS 는 잔량 취소에 성공(True)했고, 기존 코드는 그 성공만 보고
    entry_order_id 를 지워 체결된 21주를 고아로 만들었다. mock 브로커는
    미등록 주문에 False 를 주므로 취소 성공 경로는 이렇게만 재현된다.
    """
    import maps.execution.broker_adapter as ba
    from maps.common.models import AnalysisPick

    class _CancelOkBroker:
        def cancel_order(self, order_id: str) -> bool:
            return True

    monkeypatch.setattr(ba, "get_broker", lambda *a, **k: _CancelOkBroker())

    pid = _new_pick(client)
    client.post(f"/api/v1/analysis-picks/{pid}/arm")
    _seed_entry_order(client, pid, order_id="ord-kis", status="partially_filled", fill_qty=21)

    r = client.post(f"/api/v1/analysis-picks/{pid}/disarm")
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert "21주" in detail and "취소했습니다" in detail
    with client.session_factory() as s:
        pick = s.get(AnalysisPick, pid)
        assert pick.state == "BOUGHT"
        assert pick.entry_order_id == "ord-kis"   # 고아 방지의 핵심


def test_disarm_allowed_when_entry_unfilled(client) -> None:
    """미체결 주문은 취소만 확인되면 정상 해제된다(기존 동작 유지)."""
    from maps.common.models import AnalysisPick
    pid = _new_pick(client)
    client.post(f"/api/v1/analysis-picks/{pid}/arm")
    _seed_entry_order(client, pid, order_id="ord-open", status="pending", fill_qty=0)

    r = client.post(f"/api/v1/analysis-picks/{pid}/disarm")
    # mock 브로커가 취소를 확인해 주면 200, 아니면 취소 미확인 409 — 어느 쪽이든
    # 체결분이 없으므로 BOUGHT 로 가서는 안 된다.
    assert r.status_code in (200, 409)
    with client.session_factory() as s:
        assert s.get(AnalysisPick, pid).state != "BOUGHT"


# ── 기준일 만료 (2026-07-30 사고) ───────────────────────────────────────────
# 6/30 픽이 한 달째 "관찰"로 떠 있었고, 표시된 매수가는 이미 주가가 관통한 값이었다.
# 무장 거부는 서버가 해야 한다 — 텔레그램 옛 메시지의 인라인 버튼은 영구히 살아 있다.

def _aged_pick(client, trading_days: int, **overrides):
    """기준일이 N거래일 지난 픽. today 상대라 어느 날 실행해도 결과가 같다."""
    import datetime as dt
    from maps.market.trading_rules import trading_days_ago
    ref = trading_days_ago(dt.date.today(), trading_days)
    return _new_pick(client, ref_date=ref.isoformat(), **overrides)


def test_arm_rejects_stale_pick(client) -> None:
    pid = _aged_pick(client, 30)
    r = client.post(f"/api/v1/analysis-picks/{pid}/arm")
    assert r.status_code == 409
    assert "만료" in r.json()["detail"]
    # 상태는 그대로 — 거부된 무장이 부분 적용되면 안 된다
    assert client.get("/api/v1/analysis-picks").json()["picks"][0]["state"] == "WATCH"


def test_arm_allows_pick_at_cutoff_boundary(client) -> None:
    """기본 만료 기준은 5거래일 — 정확히 5거래일 된 픽은 아직 무장 가능하다."""
    pid = _aged_pick(client, 5)
    assert client.post(f"/api/v1/analysis-picks/{pid}/arm").status_code == 200


def test_arm_rejects_stale_cancelled_pick(client) -> None:
    """CANCELLED 를 재무장 허용 상태로 두는 것이 만료 구멍이 되지 않는지."""
    from maps.common.models import AnalysisPick
    pid = _aged_pick(client, 30)
    with client.session_factory() as s:
        s.get(AnalysisPick, pid).state = "CANCELLED"
        s.commit()
    assert client.post(f"/api/v1/analysis-picks/{pid}/arm").status_code == 409


def test_list_marks_stale_items(client) -> None:
    stale_id = _aged_pick(client, 30, ticker="002350", name="넥센타이어")
    fresh_id = _new_pick(client, ticker="005930")
    body = client.get("/api/v1/analysis-picks").json()
    by_id = {p["id"]: p for p in body["picks"]}

    assert by_id[stale_id]["data_stale"] is True
    assert by_id[stale_id]["stale_reason"] == "expired"
    assert by_id[stale_id]["age_trading_days"] >= 5
    assert by_id[fresh_id]["data_stale"] is False
    assert by_id[fresh_id]["stale_reason"] is None

    assert body["stale_count"] == 1
    assert body["expected_ref_date"]


def test_list_still_returns_stale_picks(client) -> None:
    """투명성 — 만료 픽을 목록에서 숨기지 않는다.

    숨기면 운영자가 삭제하려 해도 보이지 않고, 만료된 BOUGHT 포지션이 통째로 사라진다.
    """
    _aged_pick(client, 60)
    body = client.get("/api/v1/analysis-picks").json()
    assert body["total"] == 1
    assert body["picks"][0]["data_stale"] is True


def test_update_pick_reports_stale(client) -> None:
    """단건 변경 응답에도 만료가 실려야 한다(_to_item 이 cutoff 를 스스로 계산)."""
    pid = _aged_pick(client, 30)
    r = client.patch(f"/api/v1/analysis-picks/{pid}", json={"buy_price": 6140})
    assert r.status_code == 200
    assert r.json()["data_stale"] is True
