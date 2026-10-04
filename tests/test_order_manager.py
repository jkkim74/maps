"""Order audit contracts; ambiguous-order safety lives in test_execution_safety."""
import datetime as dt
from dataclasses import replace
from unittest.mock import Mock
import pytest
from maps.common.exceptions import BrokerOrderRejectedError, BrokerOrderUnknownError, ResearchStrategyError
from maps.common.models import OrderIntent, OrderLog
from maps.common.settings import MapsSettings
from maps.execution.broker_adapter import OrderSide, OrderStatus, order_log_id, raw_broker_order_id
from maps.execution.order_manager import _order_log_mode
from tests.test_execution_safety import setup, order, context


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

def test_kis_order_log_id_canonicalizes_default_product_code() -> None:
    """동일 계좌의 `12345678`과 `12345678-01` 표기는 같은 ID를 만들어야 한다."""
    submitted_at = dt.datetime(2026, 8, 10, 8, 55)

    compact = order_log_id(
        "0000000755",
        broker="kis",
        account_no="12345678",
        submitted_at=submitted_at,
    )
    explicit = order_log_id(
        "0000000755",
        broker="kis",
        account_no="12345678-01",
        submitted_at=submitted_at,
    )

    assert compact == explicit

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


def test_submit_persists_the_exact_decision_context(db, setup):
    _, manager = setup
    evidence = {"candidate": {"snapshot_id": 42}, "market": {"regime": "mixed"}}
    result = manager.submit(replace(order(), decision_context=evidence, atr14=300), context=context())
    evidence["market"]["regime"] = "changed"
    db.expire_all()
    row = db.query(OrderLog).filter_by(order_id=result.order_id).one()
    assert row.decision_context["market"]["regime"] == "mixed"
    assert row.atr14 == 300 and row.intent_id and row.code_hash and row.params_hash


def test_research_strategy_blocked(db, setup):
    broker, manager = setup
    manager.block_strategy("test")
    with pytest.raises(ResearchStrategyError):
        manager.submit(order(), context=context())
    assert not broker.filled_orders


def test_explicit_rejection_is_released_and_counted_once(db, setup):
    broker, manager = setup
    broker.place_order = Mock(side_effect=BrokerOrderRejectedError("insufficient funds"))
    with pytest.raises(BrokerOrderRejectedError):
        manager.submit(order(), context=context())
    row = db.query(OrderIntent).one()
    assert row.status == "REJECTED" and row.reserved_amount == 0
    assert db.query(OrderLog).one().status == "rejected"
    assert manager._risk._failure_counts["test"] == 1
    with pytest.raises(BrokerOrderUnknownError):
        manager.submit(order(), context=context())
    assert broker.place_order.call_count == 1


def test_submit_exit_records_reason_and_bypasses_entry_kill(db, setup):
    _, manager = setup
    manager.submit(order(), context=context())
    manager._risk.check_and_trigger("test", daily_pnl=-.05, current_mdd=0)
    result = manager.submit_exit(order(OrderSide.SELL), exit_reason="stop_loss", context=context("exit"))
    assert result.status == OrderStatus.FILLED
    assert db.query(OrderLog).filter_by(order_id=result.order_id).one().exit_reason == "stop_loss"


def test_eod_cleanup_does_not_expire_unknown_orders(db, setup):
    broker, manager = setup
    broker.place_order = Mock(side_effect=TimeoutError())
    with pytest.raises(BrokerOrderUnknownError):
        manager.submit(order(), context=context())
    manager.eod_cleanup()
    assert db.query(OrderIntent).one().status == "UNKNOWN"
