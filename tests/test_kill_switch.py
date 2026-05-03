"""RiskManager Kill Switch 원칙 테스트.

- 신규 진입 차단: 자동
- 보유 청산: 사용자 승인 필수
"""

from unittest.mock import MagicMock

import pytest

from maps.common.exceptions import KillSwitchError
from maps.risk.manager import KillSwitchReason, RiskConfig, RiskManager


@pytest.fixture
def manager() -> RiskManager:
    broker = MagicMock()
    broker.get_balance.return_value = 100_000_000
    db = MagicMock()
    cfg = RiskConfig(daily_loss_limit=0.03, mdd_limit=0.15)
    return RiskManager(broker=broker, db=db, config=cfg)


def test_kill_switch_triggers_on_daily_loss(manager: RiskManager) -> None:
    event = manager.check_and_trigger("strat_a", daily_pnl=-0.05, current_mdd=-0.05)
    assert event is not None
    assert event.reason == KillSwitchReason.DAILY_LOSS_LIMIT
    assert event.new_entry_blocked is True
    assert event.liquidation_approved is False


def test_new_entry_blocked_after_kill_switch(manager: RiskManager) -> None:
    manager.check_and_trigger("strat_a", daily_pnl=-0.05, current_mdd=0.0)
    assert manager.is_new_entry_blocked("strat_a") is True


def test_liquidation_requires_approval(manager: RiskManager) -> None:
    manager.check_and_trigger("strat_a", daily_pnl=-0.05, current_mdd=0.0)
    event = manager.approve_liquidation("strat_a", approved_by="admin")
    assert event.liquidation_approved is True
    assert event.approved_by == "admin"


def test_approve_liquidation_without_kill_switch_raises(manager: RiskManager) -> None:
    with pytest.raises(KillSwitchError):
        manager.approve_liquidation("strat_no_kill", approved_by="admin")
