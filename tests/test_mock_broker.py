"""MockBroker 단위 테스트 (Phase 4)."""

from __future__ import annotations

import pytest

from maps.common.exceptions import DuplicateOrderError, KillSwitchError
from maps.execution.broker_adapter import Order, OrderSide, OrderStatus, OrderType
from maps.execution.mock_broker import MockBroker


@pytest.fixture
def broker() -> MockBroker:
    return MockBroker(
        initial_cash=1_000_000,
        price_feed={"AAAA": 10_000, "BBBB": 5_000},
    )


def _buy(broker: MockBroker, ticker: str = "AAAA", qty: int = 10, strategy: str = "s1") -> Order:
    return Order(
        strategy_id=strategy,
        ticker=ticker,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=qty,
    )


def _sell(broker: MockBroker, ticker: str = "AAAA", qty: int = 10, strategy: str = "s1") -> Order:
    return Order(
        strategy_id=strategy,
        ticker=ticker,
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        quantity=qty,
    )


# ---------------------------------------------------------------------------
# 1. 정상 체결
# ---------------------------------------------------------------------------

def test_place_order_filled(broker: MockBroker) -> None:
    """매수 주문이 즉시 FILLED 상태로 체결된다."""
    order = _buy(broker, qty=10)
    result = broker.place_order(order)

    assert result.status == OrderStatus.FILLED
    assert result.filled_quantity == 10
    assert result.avg_price == 10_000

    balance = broker.get_account_balance()
    assert balance.cash == 1_000_000 - 10 * 10_000


# ---------------------------------------------------------------------------
# 2. 중복 주문 거부
# ---------------------------------------------------------------------------

def test_duplicate_order_error(broker: MockBroker) -> None:
    """같은 전략+종목+방향으로 당일 두 번 주문 시 DuplicateOrderError."""
    order = _buy(broker, qty=5)
    broker.place_order(order)

    with pytest.raises(DuplicateOrderError):
        broker.place_order(_buy(broker, qty=3))  # 같은 strategy+ticker+BUY


# ---------------------------------------------------------------------------
# 3. Kill Switch 차단
# ---------------------------------------------------------------------------

def test_kill_switch_blocks_order(broker: MockBroker) -> None:
    """Kill Switch 활성 후 주문 시 KillSwitchError."""
    broker.activate_kill_switch()

    with pytest.raises(KillSwitchError):
        broker.place_order(_buy(broker))


def test_kill_switch_deactivate_allows_order(broker: MockBroker) -> None:
    """Kill Switch 해제 후 주문이 정상 처리된다."""
    broker.activate_kill_switch()
    broker.deactivate_kill_switch(approved_by="admin")

    result = broker.place_order(_buy(broker))
    assert result.status == OrderStatus.FILLED


# ---------------------------------------------------------------------------
# 4. EOD 정리 후 중복 탐지 초기화
# ---------------------------------------------------------------------------

def test_eod_cleanup_resets_duplicate_detection(broker: MockBroker) -> None:
    """eod_cleanup() 후 같은 방향 주문이 다시 가능하다."""
    broker.place_order(_buy(broker, qty=5))  # 첫 번째 매수

    broker.eod_cleanup()  # 중복 탐지 초기화

    # 매도 후 다시 매수 (같은 종목이지만 eod 이후)
    broker.place_order(_sell(broker, qty=5))  # 매도
    result = broker.place_order(_buy(broker, qty=3))  # 매수 다시 허용
    assert result.status == OrderStatus.FILLED


# ---------------------------------------------------------------------------
# 5. 잔액 부족 → REJECTED
# ---------------------------------------------------------------------------

def test_insufficient_cash_rejected(broker: MockBroker) -> None:
    """현금이 부족하면 REJECTED 상태로 반환된다."""
    # 1_000_000 / 10_000 = 100주 최대 → 101주 주문
    order = _buy(broker, qty=101)
    result = broker.place_order(order)

    assert result.status == OrderStatus.REJECTED
    assert result.filled_quantity == 0
    # 잔액은 변하지 않아야 함
    assert broker.get_account_balance().cash == 1_000_000


# ---------------------------------------------------------------------------
# 6. get_position / get_account_balance
# ---------------------------------------------------------------------------

def test_get_position_after_buy(broker: MockBroker) -> None:
    """매수 후 포지션이 올바르게 기록된다."""
    broker.place_order(_buy(broker, qty=20))
    pos = broker.get_position("AAAA")

    assert pos is not None
    assert pos.quantity == 20
    assert pos.avg_price == 10_000


def test_get_position_none_when_not_held(broker: MockBroker) -> None:
    """미보유 종목은 None을 반환한다."""
    assert broker.get_position("CCCC") is None


# ---------------------------------------------------------------------------
# 7. H-1: 현재가 기반 포지션 평가
# ---------------------------------------------------------------------------

def test_get_position_includes_current_price_from_feed(broker: MockBroker) -> None:
    """price_feed 에 현재가가 있으면 get_position() 의 current_price 에 반영된다."""
    broker.place_order(_buy(broker, qty=10))  # 10,000원에 매수
    broker.set_price("AAAA", 12_000)          # 현재가 12,000원으로 갱신

    pos = broker.get_position("AAAA")

    assert pos is not None
    assert pos.avg_price == 10_000
    assert pos.current_price == 12_000


def test_get_account_balance_uses_current_price_when_available() -> None:
    """price_feed 가 있으면 포지션 가치는 현재가 × 수량으로 계산된다."""
    broker = MockBroker(initial_cash=1_000_000, price_feed={"AAAA": 10_000})
    broker.place_order(_buy(broker, qty=10))  # 10,000원에 10주 매수 → 잔금 900,000원

    # 현재가를 -10% 하락 반영
    broker.set_price("AAAA", 9_000)
    balance = broker.get_account_balance()

    assert balance.positions_value == 10 * 9_000   # 시가 평가
    assert balance.total_value == 900_000 + 90_000  # 잔금 + 시가 포지션


def test_get_account_balance_falls_back_to_avg_price_when_no_feed() -> None:
    """price_feed 가 없으면 포지션 가치를 매입가 기준으로 계산한다."""
    broker = MockBroker(initial_cash=1_000_000, price_feed={"AAAA": 10_000})
    broker.place_order(_buy(broker, qty=10))
    # price_feed 를 비워 폴백 동작 확인
    broker._price_feed.clear()

    balance = broker.get_account_balance()

    assert balance.positions_value == 10 * 10_000  # 매입가 기준 폴백
