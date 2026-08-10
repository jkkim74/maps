"""OrderManager 테스트 (Phase 4)."""

from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from maps.common.db import Base
from maps.common.exceptions import BrokerAdapterError, DuplicateOrderError, KillSwitchError, ResearchStrategyError
from maps.common.models import OrderLog
from maps.execution.broker_adapter import (
    AccountBalance,
    Order,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
    order_log_id,
    raw_broker_order_id,
)
from maps.execution.mock_broker import MockBroker
from maps.common.settings import MapsSettings
from maps.execution.order_manager import OrderManager, _order_log_mode
from maps.risk.manager import RiskConfig, RiskManager


@pytest.fixture
def broker() -> MockBroker:
    return MockBroker(
        initial_cash=10_000_000,
        price_feed={"AAAA": 10_000},
    )


@pytest.fixture
def risk(broker: MockBroker) -> RiskManager:
    return RiskManager(broker=broker, db=MagicMock(), config=RiskConfig())


@pytest.fixture
def manager(broker: MockBroker, risk: RiskManager) -> OrderManager:
    return OrderManager(
        broker=broker,
        risk=risk,
        db=MagicMock(),
        research_strategies={"research_strat"},
    )


def _buy(ticker: str = "AAAA", strategy: str = "live_strat") -> Order:
    return Order(
        strategy_id=strategy,
        ticker=ticker,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=10,
        limit_price=10_000,
    )


def test_kis_order_log_id_includes_account_and_kst_day() -> None:
    """같은 KIS ODNO라도 거래일이 다르면 감사 ID가 충돌하지 않아야 한다."""
    first = order_log_id(
        "0000000755",
        broker="kis",
        account_no="11111111-01",
        submitted_at=dt.datetime(2026, 8, 6, 8, 55),
    )
    later = order_log_id(
        "0000000755",
        broker="kis",
        account_no="11111111-01",
        submitted_at=dt.datetime(2026, 8, 10, 8, 55),
    )

    assert first != later
    assert first.endswith(":20260806:0000000755")
    assert later.endswith(":20260810:0000000755")
    assert "11111111" not in first
    assert raw_broker_order_id(later) == "0000000755"


def test_non_kis_order_log_id_is_unchanged() -> None:
    """Mock 등 ODNO 재사용 문제가 없는 기존 브로커 ID는 바꾸지 않는다."""
    assert order_log_id(
        "mock-1",
        broker="mock",
        account_no="",
        submitted_at=dt.datetime(2026, 8, 10),
    ) == "mock-1"


def test_submit_namespaces_kis_order_id_in_result_and_audit_log(db, monkeypatch) -> None:
    """KIS 제출 결과와 감사 행은 같은 전역 유일 ID를 사용해야 한다."""
    submitted_at = dt.datetime(2026, 8, 10, 8, 55)
    live_broker = MagicMock()
    live_broker.get_account_balance.return_value = AccountBalance(
        cash=10_000_000,
        positions_value=0,
    )
    live_broker.place_order.return_value = OrderResult(
        order_id="0000000755",
        strategy_id="live_strat",
        ticker="AAAA",
        side=OrderSide.BUY,
        status=OrderStatus.PENDING,
        submitted_at=submitted_at,
    )
    settings = MapsSettings(
        maps_broker_mode="kis",
        kis_account_no="11111111-01",
    )
    monkeypatch.setattr("maps.execution.order_manager.get_settings", lambda: settings)
    manager = OrderManager(
        broker=live_broker,
        risk=RiskManager(broker=live_broker, db=db, config=RiskConfig()),
        db=db,
    )

    result = manager.submit(_buy())

    row = db.query(OrderLog).one()
    assert result.order_id.endswith(":20260810:0000000755")
    assert row.order_id == result.order_id


# ---------------------------------------------------------------------------
# 1. Research 전략 차단
# ---------------------------------------------------------------------------

def test_research_strategy_blocked(manager: OrderManager) -> None:
    """Research 단계 전략의 자동 주문 → ResearchStrategyError."""
    order = _buy(strategy="research_strat")

    with pytest.raises(ResearchStrategyError) as exc_info:
        manager.submit(order)

    assert exc_info.value.strategy_id == "research_strat"


def test_non_research_strategy_allowed(manager: OrderManager) -> None:
    """Research 차단 목록에 없는 전략은 정상 주문 가능."""
    order = _buy(strategy="live_strat")
    result = manager.submit(order)
    assert result.status == OrderStatus.FILLED


# ---------------------------------------------------------------------------
# 2. Kill Switch 전파
# ---------------------------------------------------------------------------

def test_kill_switch_propagates(
    manager: OrderManager,
    broker: MockBroker,
    risk: RiskManager,
) -> None:
    """Kill Switch 발동 후 submit() 이 KillSwitchError를 전파한다."""
    # RiskManager를 통해 Kill Switch 발동
    risk.check_and_trigger("live_strat", daily_pnl=-0.05, current_mdd=0.0)

    order = _buy(strategy="live_strat")
    with pytest.raises(KillSwitchError):
        manager.submit(order)


# ---------------------------------------------------------------------------
# 3. eod_cleanup 위임
# ---------------------------------------------------------------------------

def test_eod_cleanup_delegates_to_broker(
    manager: OrderManager,
    broker: MockBroker,
) -> None:
    """eod_cleanup() 이 broker.eod_cleanup() 을 호출한다."""
    # 첫 주문 후 중복 탐지 등록
    manager.submit(_buy())

    manager.eod_cleanup()

    # EOD 후 같은 방향 주문이 다시 가능해야 함
    result = manager.submit(_buy())
    assert result.status == OrderStatus.FILLED


# ---------------------------------------------------------------------------
# 4. 실패 시 RiskManager 카운터 증가
# ---------------------------------------------------------------------------

def test_failure_increments_risk_counter(
    manager: OrderManager,
    broker: MockBroker,
    risk: RiskManager,
) -> None:
    """broker.place_order가 예외를 던지면 on_order_failure가 호출된다."""
    # Kill Switch를 broker 레벨에서 직접 활성화
    broker.activate_kill_switch()

    with pytest.raises(KillSwitchError):
        # submit() → check_before_order 통과 → broker.place_order → KillSwitchError
        # → on_order_failure 호출
        manager.submit(_buy(strategy="s2"))

    # 실패 카운터가 1 이상이어야 함
    assert risk._failure_counts.get("s2", 0) >= 1


def test_submit_exit_bypasses_new_entry_kill_switch(
    manager: OrderManager,
    broker: MockBroker,
    risk: RiskManager,
) -> None:
    manager.submit(_buy())
    risk.check_and_trigger("live_strat", daily_pnl=-0.05, current_mdd=0.0)

    result = manager.submit_exit(Order(
        strategy_id="live_strat",
        ticker="AAAA",
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        quantity=10,
        current_price=10_000,
    ))

    assert result.status == OrderStatus.FILLED


def test_submit_exit_records_exit_reason(broker: MockBroker) -> None:
    """청산 사유가 order_log에 남아야 한다 — 이전에는 로그에만 있어 사후 검증이 불가했다."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        manager = OrderManager(
            broker=broker, risk=RiskManager(broker=broker, db=db, config=RiskConfig()), db=db,
        )
        manager.submit(_buy())
        manager.submit_exit(
            Order(
                strategy_id="live_strat",
                ticker="AAAA",
                side=OrderSide.SELL,
                order_type=OrderType.MARKET,
                quantity=10,
                current_price=9_000,
            ),
            exit_reason="stop_loss",
        )

        rows = {r.side: r for r in db.query(OrderLog).all()}
        assert rows["sell"].exit_reason == "stop_loss"
        assert rows["buy"].exit_reason is None   # 매수에는 청산 사유가 없다
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_buy_records_entry_atr(broker: MockBroker) -> None:
    """매수 주문에 진입 시점 ATR 이 남아야 한다.

    청산·화면이 이 값을 재사용해 손절가를 고정한다. 기록이 없으면 그날의 ATR 로
    다시 계산돼 손절폭이 사이징 가정과 어긋난다(2026-07-31 확인).
    """
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        manager = OrderManager(
            broker=broker, risk=RiskManager(broker=broker, db=db, config=RiskConfig()), db=db,
        )
        order = _buy()
        order.atr14 = 1_874.4
        manager.submit(order)

        row = db.query(OrderLog).filter(OrderLog.side == "buy").one()
        assert row.atr14 == pytest.approx(1_874.4)
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


# ---------------------------------------------------------------------------
# 5. order_log.mode 라벨 — 페이퍼 계좌 체결은 'live'가 아니다
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "kwargs, expected",
    [
        ({"maps_broker_mode": "mock", "maps_live_trading_enabled": True}, "mock"),
        # KIS 모의투자(paper) — 주문은 나가지만 실제 돈은 아니다
        ({"maps_broker_mode": "kis", "maps_live_trading_enabled": True, "kis_real_trading": False}, "mock"),
        ({"maps_broker_mode": "kis", "maps_live_trading_enabled": True, "kis_real_trading": True}, "live"),
        ({"maps_broker_mode": "kis", "maps_live_trading_enabled": False, "kis_real_trading": True}, "mock"),
    ],
)
def test_order_log_mode_marks_only_real_money_as_live(monkeypatch, kwargs, expected) -> None:
    monkeypatch.setattr(
        "maps.execution.order_manager.get_settings",
        lambda: MapsSettings(**kwargs),
    )
    assert _order_log_mode() == expected
