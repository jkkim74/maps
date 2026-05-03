"""RiskManager 신규 메서드 테스트 (Phase 4).

기존 Kill Switch 테스트는 test_kill_switch.py 참조.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from maps.common.exceptions import ExposureCapError, KillSwitchError
from maps.execution.broker_adapter import AccountBalance, Order, OrderSide, OrderType
from maps.risk.manager import KillSwitchReason, RiskConfig, RiskManager


@pytest.fixture
def manager() -> RiskManager:
    broker = MagicMock()
    broker.get_balance.return_value = 10_000_000
    db = MagicMock()
    cfg = RiskConfig(daily_loss_limit=0.015, mdd_limit=0.15, position_size_limit=0.10)
    return RiskManager(broker=broker, db=db, config=cfg)


def _order(ticker: str = "AAAA", qty: int = 10, price: float | None = None) -> Order:
    return Order(
        strategy_id="strat_a",
        ticker=ticker,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=qty,
        limit_price=price,
    )


def _account(cash: float = 10_000_000, positions: float = 0.0) -> AccountBalance:
    return AccountBalance(cash=cash, positions_value=positions)


# ---------------------------------------------------------------------------
# 1. 일일 손실 한도 → Kill Switch
# ---------------------------------------------------------------------------

def test_daily_loss_trigger(manager: RiskManager) -> None:
    """일일 손실 1.5% 초과 시 check_before_order가 KillSwitchError를 발생시킨다."""
    order = _order()
    account = _account()

    with pytest.raises(KillSwitchError):
        manager.check_before_order(order, account, daily_pnl=-0.02)  # -2% > -1.5%

    assert manager.is_new_entry_blocked("strat_a")


def test_daily_loss_within_limit_ok(manager: RiskManager) -> None:
    """일일 손실이 한도 이내면 통과한다."""
    order = _order()
    account = _account()
    # -1.4% < -1.5% → 통과 (예외 없음)
    manager.check_before_order(order, account, daily_pnl=-0.01)


# ---------------------------------------------------------------------------
# 2. 연속 실패 5회 → Kill Switch
# ---------------------------------------------------------------------------

def test_consec_failure_trigger(manager: RiskManager) -> None:
    """5회 연속 주문 실패 시 Kill Switch가 자동 발동된다."""
    for _ in range(4):
        event = manager.on_order_failure("strat_a")
        assert event is None  # 5회 미만 → 미발동

    event = manager.on_order_failure("strat_a")  # 5회째
    assert event is not None
    assert event.reason == KillSwitchReason.CONSECUTIVE_FAILURE
    assert manager.is_new_entry_blocked("strat_a")


def test_on_order_success_resets_counter(manager: RiskManager) -> None:
    """성공 시 실패 카운터가 리셋된다."""
    for _ in range(3):
        manager.on_order_failure("strat_a")

    manager.on_order_success("strat_a")  # 리셋

    for _ in range(4):
        event = manager.on_order_failure("strat_a")
        assert event is None  # 리셋 후 4회 → 미발동


# ---------------------------------------------------------------------------
# 3. deactivate (= release)
# ---------------------------------------------------------------------------

def test_deactivate_requires_approval(manager: RiskManager) -> None:
    """Kill Switch 발동 후 deactivate(approved_by) 로 해제할 수 있다."""
    manager.check_and_trigger("strat_a", daily_pnl=-0.05, current_mdd=0.0)
    assert manager.is_new_entry_blocked("strat_a")

    manager.deactivate("strat_a", approved_by="admin")
    assert not manager.is_new_entry_blocked("strat_a")


def test_deactivate_without_active_kill_switch_raises(manager: RiskManager) -> None:
    """Kill Switch가 없는 전략에 deactivate 시도 → KillSwitchError."""
    with pytest.raises(KillSwitchError):
        manager.deactivate("no_kill_strat", approved_by="admin")


# ---------------------------------------------------------------------------
# 4. 단일 종목 노출 한도 초과 → ExposureCapError
# ---------------------------------------------------------------------------

def test_exposure_cap(manager: RiskManager) -> None:
    """단일 종목 주문 금액이 총 자산의 10%를 초과하면 ExposureCapError."""
    # 총 자산 = 10_000_000, 10% = 1_000_000
    # 주문: 200주 @ 10_000 = 2_000_000 (20%) → 초과
    order = _order(qty=200, price=10_000)
    account = _account(cash=10_000_000)

    with pytest.raises(ExposureCapError):
        manager.check_before_order(order, account)


def test_exposure_cap_within_limit_ok(manager: RiskManager) -> None:
    """노출이 한도 이내면 통과한다."""
    # 주문: 50주 @ 10_000 = 500_000 (5%) → 통과
    order = _order(qty=50, price=10_000)
    account = _account(cash=10_000_000)
    manager.check_before_order(order, account)  # 예외 없음
