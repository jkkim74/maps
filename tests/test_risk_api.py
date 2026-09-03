from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import datetime as dt

import maps.common.models  # noqa: F401
from maps.api import risk
from maps.common.db import Base
from maps.common.exceptions import BrokerAdapterError
from maps.common.models import AnalysisPick, HistoricalOHLCV, KillSwitchLog, OrderLog
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
            "quantity": 2,
            "market_value": 104000.0,
        }
    ]
    assert data["max_exposure_pct"] == 0.104
    assert data["position_count"] == 1
    assert data["active_kill_count"] == 0


def test_position_count_is_holdings_only_and_kills_reported_separately(db, monkeypatch) -> None:
    """보유 1건 + 활성 Kill 3건이어도 position_count 는 1이어야 한다.

    과거에는 max(len(active_kills), 보유수) 로 합쳐서, Kill 이 많으면 화면의
    '보유 종목' KPI 가 실제 보유 행 수보다 커졌다.
    """
    for strategy_id in ("pullback_v3", "donchian_v1", "ath_breakout_v1"):
        db.add(KillSwitchLog(
            strategy_id=strategy_id,
            event_type="trigger",
            reason="daily_loss",
            new_entry_blocked=True,
        ))
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

    class FakeBroker:
        def get_account_balance(self):
            return AccountBalance(cash=900_000, positions_value=104_000)

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

    response = risk.get_risk(db)

    assert len(response.holdings) == 1
    assert response.position_count == 1
    assert response.active_kill_count == 3


def test_active_kills_lists_only_triggered_strategies(db, monkeypatch) -> None:
    """화면 해제 버튼용 active_kills — 해제된 전략은 빠지고 발동 중만 남아야 한다."""
    db.add(KillSwitchLog(
        strategy_id="ath_breakout_v1",
        event_type="trigger",
        reason="consecutive_failures",
        new_entry_blocked=True,
    ))
    db.add(KillSwitchLog(
        strategy_id="pullback_v3",
        event_type="trigger",
        reason="daily_loss",
        new_entry_blocked=True,
    ))
    db.add(KillSwitchLog(
        strategy_id="pullback_v3",
        event_type="deactivate",
        reason="daily_loss",
        new_entry_blocked=False,
    ))
    db.commit()

    monkeypatch.setattr(risk, "get_broker", lambda: _FailingBroker())

    response = risk.get_risk(db)

    assert [k.strategy_id for k in response.active_kills] == ["ath_breakout_v1"]
    assert response.active_kills[0].reason == "consecutive_failures"
    assert response.active_kills[0].created_at.endswith("+00:00")


def test_broker_holdings_expose_quantity_and_market_value(db, monkeypatch) -> None:
    """화면이 '113주 · 평가 4,248,800원' 을 찍을 수 있어야 한다."""
    class FakeBroker:
        def get_account_balance(self):
            return AccountBalance(cash=1_000_000, positions_value=4_248_800)

        def _fetch_positions_and_balance(self):
            return {
                "089860": Position(
                    "089860",
                    113,
                    36_600,
                    name="롯데렌탈",
                    current_price=37_600,
                    evaluation_value=4_248_800,
                )
            }, self.get_account_balance()

    monkeypatch.setattr(risk, "get_broker", lambda: FakeBroker())

    holdings, _max_exposure, _count, status, _error = risk._broker_holdings(db)

    assert status == "ok"
    assert holdings[0].quantity == 113
    assert holdings[0].market_value == 4_248_800.0


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

    holdings, _max_exposure, count, status, error = risk._broker_holdings(db)

    assert count == 1
    assert status == "ok"
    assert error is None
    assert holdings[0].strategy_id == "donchian_v2"
    # 352,500 × 0.90 = 317,250 → 호가 500원 단위로 내림 (317,250 은 유효 호가가 아니다)
    assert holdings[0].stop_price == 317_000.0


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

    holdings, max_exposure, count, _status, _error = risk._broker_holdings(db)

    assert count == 1
    assert max_exposure == 7_252_000 / 8_152_000
    assert holdings[0].ticker == "009150"
    assert holdings[0].strategy_id == "donchian_v2"
    # 2,067,000 × 0.90 = 1,860,300 → 호가 1,000원 단위로 내림
    assert holdings[0].stop_price == 1_860_000.0


class _FailingBroker:
    def get_account_balance(self):
        raise BrokerAdapterError("KIS request failed after 3 attempts: connection refused")


def test_broker_holdings_falls_back_to_bought_picks_when_broker_unavailable(db, monkeypatch) -> None:
    db.add(AnalysisPick(
        ref_date=dt.date(2026, 7, 3),
        ticker="004490",
        name="세방전지",
        buy_price=53_900,
        target_price=54_900,
        stop_price=46_800,
        state="BOUGHT",
        strategy_trade_enabled=True,
    ))
    db.add(HistoricalOHLCV(
        ticker="004490",
        date=dt.date(2026, 7, 3),
        open=53_500, high=54_500, low=53_000, close=54_000, volume=100_000,
    ))
    db.commit()

    monkeypatch.setattr(risk, "get_broker", lambda: _FailingBroker())

    holdings, max_exposure, count, status, error = risk._broker_holdings(db)

    assert status == "fallback"
    assert "connection refused" in error
    assert count == 1
    assert max_exposure == 0.0
    assert holdings[0].ticker == "004490"
    assert holdings[0].name == "세방전지"
    assert holdings[0].entry_price == 53_900.0
    assert holdings[0].current_price == 54_000.0
    assert holdings[0].stop_price == 46_800.0
    assert holdings[0].pnl_pct == pytest.approx(54_000 / 53_900 - 1.0)
    # 실시간 잔고가 없으므로 수량·평가금액은 비워 둔다 — 화면이 그 줄을 숨긴다.
    assert holdings[0].quantity == 0
    assert holdings[0].market_value is None


def test_holding_stop_uses_entry_atr_recorded_on_the_buy_order(db, monkeypatch) -> None:
    """화면 손절가는 매수 주문에 기록된 진입 시점 ATR 을 쓴다.

    OHLCV 로 매번 다시 계산하면 두 가지가 어긋난다.
      1. 보유 중 ATR 이 커지면 손절가가 밀려 사이징이 가정한 위험을 넘는다.
      2. 화면(예전 20봉)과 청산 판정(400봉)이 서로 다른 ATR 을 써서 표시가 거짓이 된다.

    pullback_v3 · 진입 36,600 · ATR 1,874.4 → 36,600 − 2×1,874.4 = 32,851.2.
    고정 5%(34,770)보다 넓으므로 ATR 이 이기지만, 손절폭이 10.24% 라 **상한
    10%(32,940)에 걸린다** → 호가 50원 내림 32,900.
    (상한 도입 전에는 32,850 이었다. 이 종목이 실제로 상한을 스치는 경계 사례다.)
    """
    db.add(OrderLog(
        order_id="filled-089860",
        strategy_id="pullback_v3",
        ticker="089860",
        side="buy",
        qty=113,
        order_price=37_400,
        fill_price=36_600,
        fill_qty=113,
        status="filled",
        atr14=1_874.4,
    ))
    db.commit()

    class FakeBroker:
        def get_account_balance(self):
            return AccountBalance(cash=1_000_000, positions_value=4_474_800)

        def _fetch_positions_and_balance(self):
            return {
                "089860": Position(
                    "089860", 113, 36_600,
                    name="롯데렌탈", current_price=39_600, evaluation_value=4_474_800,
                )
            }, self.get_account_balance()

    monkeypatch.setattr(risk, "get_broker", lambda: FakeBroker())
    # OHLCV 기반 폴백이 쓰이면 즉시 드러나도록 다른 값을 돌려주게 만든다.
    monkeypatch.setattr(risk, "_atr14_for_ticker", lambda *a, **k: 9_999.0)

    holdings, _max_exposure, _count, status, _error = risk._broker_holdings(db)

    assert status == "ok"
    assert holdings[0].stop_price == 32_900.0


def test_broker_holdings_unavailable_without_fallback_records(db, monkeypatch) -> None:
    monkeypatch.setattr(risk, "get_broker", lambda: _FailingBroker())

    holdings, max_exposure, count, status, error = risk._broker_holdings(db)

    assert holdings == []
    assert count == 0
    assert max_exposure == 0.0
    assert status == "unavailable"
    assert "KIS request failed" in error
