"""OrderManager 테스트 (Phase 4)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from maps.common.exceptions import BrokerAdapterError, DuplicateOrderError, KillSwitchError, ResearchStrategyError
from maps.common.models import OrderLog
from maps.execution.broker_adapter import (
    AccountBalance,
    Order,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
)
from maps.execution.mock_broker import MockBroker
from maps.execution.order_manager import OrderManager
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
