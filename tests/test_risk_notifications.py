"""Risk notification tests."""

from __future__ import annotations

from unittest.mock import MagicMock

from maps.execution.broker_adapter import AccountBalance
from maps.risk.manager import RiskConfig, RiskManager


class FakeNotifier:
    def __init__(self) -> None:
        self.kill_events: list[dict] = []

    def send_kill_switch(self, **kwargs):
        self.kill_events.append(kwargs)
        return True


def test_kill_switch_trigger_sends_notification(db) -> None:
    broker = MagicMock()
    broker.get_account_balance.return_value = AccountBalance(cash=1_000_000, positions_value=0)
    notifier = FakeNotifier()
    risk = RiskManager(broker=broker, db=db, config=RiskConfig(), notifier=notifier)

    risk.check_and_trigger("s1", daily_pnl=-0.05, current_mdd=0.0)

    assert len(notifier.kill_events) == 1
    assert notifier.kill_events[0]["strategy_id"] == "s1"
    assert notifier.kill_events[0]["event_type"] == "trigger"
