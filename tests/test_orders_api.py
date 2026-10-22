"""Read-only order status must describe the configured account's real evidence."""
from __future__ import annotations

import datetime as dt

import pytest

from maps.api import orders
from maps.common.models import ExecutionAccountState, KillSwitchLog, OrderLog
from maps.common.settings import MapsSettings
from maps.execution.safety import account_key, execution_environment

NOW = dt.datetime(2026, 10, 22, 3)


@pytest.fixture
def settings(monkeypatch):
    """Supply only synthetic configuration and a fixed UTC check time."""
    value = MapsSettings(_env_file=None, maps_broker_mode="mock", kis_account_no="12345678-01",
                         maps_live_trading_enabled=True, maps_dry_run=False)
    monkeypatch.setattr(orders, "get_settings", lambda: value)
    monkeypatch.setattr(orders, "utcnow", lambda: NOW, raising=False)
    return value


def ready_account(db, settings, **changes):
    """Persist reconciled account evidence without any broker interaction."""
    values = dict(account_key=account_key(settings), environment=execution_environment(settings),
                  status="READY", checked_at=NOW, block_reasons=[], killed=False)
    values.update(changes)
    row = ExecutionAccountState(**values)
    db.add(row)
    db.commit()
    return row


def order(db, settings, order_id, **changes):
    """Create an independently specified audit record."""
    values = dict(order_id=order_id, account_key=account_key(settings),
                  environment=execution_environment(settings), strategy_id="pullback_v3",
                  ticker="005930", side="buy", qty=10, fill_qty=0,
                  order_price=100.0, status="pending", created_at=NOW)
    values.update(changes)
    row = OrderLog(**values)
    db.add(row)
    db.flush()
    return row


@pytest.mark.parametrize("setting,value,reason", [
    ("maps_live_trading_enabled", False, "trading_disabled"),
    ("maps_dry_run", True, "dry_run"),
])
def test_config_blocks_even_with_ready_account(db, settings, setting, value, reason):
    ready_account(db, settings)
    setattr(settings, setting, value)
    result = orders.get_orders(db).model_dump()
    assert result["execution_state"] == "blocked"
    assert result["auto_order_active"] is False
    assert reason in result["block_reasons"]


def test_real_trading_requires_confirmation(db, settings):
    settings.maps_broker_mode = "kis"
    settings.kis_real_trading = True
    settings.maps_confirm_real_trading = False
    ready_account(db, settings)
    result = orders.get_orders(db).model_dump()
    assert result["execution_state"] == "blocked"
    assert "real_trading_unconfirmed" in result["block_reasons"]


def test_missing_account_is_unknown_never_active(db, settings):
    result = orders.get_orders(db).model_dump()
    assert result["execution_state"] == "unknown"
    assert result["auto_order_active"] is False
    assert "account_unobserved" in result["block_reasons"]
    assert result["checked_at"] is None


@pytest.mark.parametrize("changes,reason,state", [
    ({"checked_at": NOW - dt.timedelta(seconds=121)}, "reconciliation_stale", "unknown"),
    ({"checked_at": None}, "reconciliation_stale", "unknown"),
    ({"checked_at": NOW + dt.timedelta(seconds=10)}, "reconciliation_stale", "unknown"),
    ({"status": "WARMUP", "block_reasons": ["account_warmup"]}, "account_warmup", "blocked"),
    ({"status": "UNKNOWN"}, "account_status_unknown", "unknown"),
    ({"environment": "kis_paper"}, "account_environment_mismatch", "unknown"),
    ({"killed": True}, "account_kill_switch", "blocked"),
])
def test_incomplete_or_blocked_account_cannot_be_ready(db, settings, changes, reason, state):
    ready_account(db, settings, **changes)
    result = orders.get_orders(db).model_dump()
    assert result["execution_state"] == state
    assert result["auto_order_active"] is False
    assert reason in result["block_reasons"]


def test_fresh_ready_account_and_exact_max_age_are_ready(db, settings):
    ready_account(db, settings, checked_at=NOW - dt.timedelta(seconds=120))
    result = orders.get_orders(db).model_dump()
    assert result["execution_state"] == "ready"
    assert result["auto_order_active"] is True
    assert result["block_reasons"] == []
    assert result["checked_at"] == "2026-10-22T02:58:00+00:00"


def test_strategy_kills_are_separate_and_latest_not_limited_to_200(db, settings):
    ready_account(db, settings)
    db.add(KillSwitchLog(account_key=account_key(settings), scope="strategy", strategy_id="blocked",
                         event_type="approved", reason="daily_loss", created_at=NOW - dt.timedelta(days=2)))
    db.add(KillSwitchLog(account_key=None, scope=None, strategy_id="legacy",
                         event_type="trigger", reason="daily_loss", created_at=NOW))
    for i in range(205):
        db.add(KillSwitchLog(account_key=account_key(settings), scope="strategy", strategy_id="released",
                             event_type="deactivate", reason="manual", created_at=NOW))
    db.add(KillSwitchLog(account_key="other", scope="account", strategy_id="other",
                         event_type="trigger", reason="daily_loss", created_at=NOW))
    db.commit()
    result = orders.get_orders(db).model_dump()
    assert result["execution_state"] == "ready"
    assert result["auto_order_active"] is True
    assert set(result["strategy_blocks"]) == {"blocked", "legacy"}
    assert "daily_loss" in result["strategy_blocks"]["blocked"]


def test_current_account_log_kill_blocks_account_without_strategy_conflation(db, settings):
    ready_account(db, settings)
    db.add(KillSwitchLog(account_key=account_key(settings), scope="account", strategy_id=None,
                         event_type="trigger", reason="account_loss_limit", created_at=NOW))
    db.commit()
    result = orders.get_orders(db).model_dump()
    assert result["execution_state"] == "blocked"
    assert "account_kill_switch" in result["block_reasons"]
    assert result["strategy_blocks"] == {}


def test_lists_are_exact_account_environment_scoped_and_preserve_canceled_fills(db, settings):
    order(db, settings, "own-fill", status="CANCELLED", fill_qty=4, fill_price=102)
    order(db, settings, "own-expired", status="expired")
    for key, env, prefix in [("other", "mock", "foreign"), (None, None, "legacy"),
                              (account_key(settings), "kis_paper", "wrong-env")]:
        for status, suffix in [("pending", "pending"), ("filled", "fill"), ("expired", "expired")]:
            order(db, settings, prefix + suffix, account_key=key, environment=env,
                  status=status, fill_qty=10 if status == "filled" else 0, fill_price=500)
    db.commit()
    result = orders.get_orders(db)
    assert result.pending == []
    assert [r.order_id for r in result.fills_today] == ["own-fill"]
    assert [r.order_id for r in result.expired] == ["own-expired"]
    assert result.slippage.model_dump()["actual"] == pytest.approx(.02)
    assert result.slippage.model_dump()["sample_count"] == 1


def test_pending_unbounded_by_recent_history_includes_partial_remaining_and_unknown(db, settings):
    order(db, settings, "old-pending", created_at=NOW - dt.timedelta(days=30))
    order(db, settings, "old-partial", status="PARTIALLY_FILLED", fill_qty=4,
          fill_price=100, created_at=NOW - dt.timedelta(days=9))
    order(db, settings, "unknown", status="UNKNOWN")
    order(db, settings, "no-remainder", status="PARTIAL", fill_qty=10, fill_price=100)
    for i in range(110):
        order(db, settings, str(i), status="filled", fill_qty=10, fill_price=100)
    db.commit()
    result = orders.get_orders(db)
    pending = {r.order_id: r.model_dump() for r in result.pending}
    assert set(pending) == {"old-pending", "old-partial", "unknown"}
    assert pending["old-partial"]["remaining_qty"] == 6
    assert pending["old-partial"]["fill_qty"] == 4
    assert len(result.fills_today) == 111  # Includes cumulative fills beyond 100 rows.


def test_today_and_slippage_use_kst_created_day_not_fill_status_or_week(db, settings):
    order(db, settings, "yesterday", status="filled", fill_qty=10, fill_price=150,
          created_at=dt.datetime(2026, 10, 21, 14, 59, 59))
    order(db, settings, "midnight", status="CANCELED", fill_qty=2, fill_price=90,
          created_at=dt.datetime(2026, 10, 21, 15))
    order(db, settings, "today", status="filled", fill_qty=10, fill_price=120)
    order(db, settings, "tomorrow", status="filled", fill_qty=10, fill_price=200,
          created_at=dt.datetime(2026, 10, 22, 15))
    db.commit()
    result = orders.get_orders(db)
    assert {r.order_id for r in result.fills_today} == {"midnight", "today"}
    assert next(r for r in result.fills_today if r.order_id == "midnight").created_at.endswith("+00:00")
    assert result.slippage.model_dump()["actual"] == pytest.approx(.15)
    assert result.slippage.model_dump()["sample_count"] == 2
    assert result.slippage.large_cap_actual is None
    assert result.slippage.mid_small_actual is None


def test_absent_samples_stay_null_and_assumptions_stay_separate(db, settings):
    order(db, settings, "no-price", status="filled", fill_qty=10, fill_price=None)
    db.commit()
    result = orders.get_orders(db).model_dump()["slippage"]
    assert result["actual"] is None
    assert result["sample_count"] == 0
    assert result["large_cap_actual"] is None
    assert result["mid_small_actual"] is None
    assert result["large_cap_assumed"] == .0005
    assert result["mid_small_assumed"] == .0015


def test_reconciliation_api_excludes_foreign_legacy_and_other_environment(db, settings):
    order(db, settings, "own", status="filled", fill_qty=10)
    order(db, settings, "foreign", account_key="other", status="filled", fill_qty=10)
    order(db, settings, "legacy", account_key=None, environment=None)
    order(db, settings, "wrong-env", environment="kis_paper")
    db.commit()
    assert orders.get_reconciliation(days=30, db=db).total_orders == 1
