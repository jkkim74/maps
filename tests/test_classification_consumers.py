"""Classification snapshots must not weaken exposure or as-of boundaries."""

import datetime as dt
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException

from maps.common.exceptions import ExposureCapError
from maps.common.settings import get_settings
from maps.execution.broker_adapter import AccountBalance, Order, OrderSide, OrderType, PendingOrder, Position
from maps.market.trading_rules import previous_trading_day
from maps.risk.manager import RiskConfig, RiskManager


def _expected_date() -> dt.date:
    return previous_trading_day(dt.datetime.now(ZoneInfo("Asia/Seoul")).date(),
                               extra_closed_dates=get_settings().krx_closed_dates)


def _publish(db, members, *, kind="theme", ref_date=None, published_at=None, catalog=None):
    from maps.data.classifications import ClassificationPayload, ClassificationRepository

    repo = ClassificationRepository(db)
    day = ref_date or _expected_date()
    codes = {c for items in members.values() for c in items}
    payload = ClassificationPayload(kind, "test", day, list(members),
                                    catalog or {c: c for c in codes} or {"unused": "unused"}, members)
    run = repo.publish(repo.start(kind, "test", day), payload)
    if published_at:
        run.published_at = published_at
        db.commit()
    return run


def _manager(db, kind="theme") -> RiskManager:
    cfg = RiskConfig(classification_snapshot_enforced=True, position_size_limit=1.0,
                     **{f"{kind}_exposure_limit_enabled": True, f"{kind}_exposure_limit": .025})
    broker = MagicMock()
    broker.get_position_details.return_value = {}
    broker.get_open_orders.return_value = []
    return RiskManager(broker, db, cfg, notifier=MagicMock())


def _order() -> Order:
    return Order("strategy", "AAA", OrderSide.BUY, OrderType.LIMIT, 10, limit_price=10000)


@pytest.mark.parametrize("kind", ["sector", "theme"])
def test_missing_snapshot_blocks_only_classification_checked_entries(db, kind):
    manager = _manager(db, kind)
    balance = AccountBalance(10_000_000, 0)
    with pytest.raises(ExposureCapError, match=f"{kind}_classification_stale"):
        manager.check_before_order(_order(), balance)
    manager.check_before_order(_order(), balance, check_classification_limits=False)
    with pytest.raises(ExposureCapError, match="insufficient_cash"):
        manager.check_before_order(_order(), AccountBalance(1, 9_999_999), check_classification_limits=False)


def test_multi_theme_includes_full_held_and_pending_values_once(db):
    _publish(db, {"AAA": ["AI", "HBM"], "BBB": ["HBM"], "CCC": ["HBM"]})
    manager = _manager(db)
    held = {"BBB": Position("BBB", 10, 10000, current_price=10000)}
    pending = [PendingOrder("pending", "CCC", OrderSide.BUY, 10, 10, 10000)]
    # 100k new + 100k held = 2%; adding 100k pending exceeds 2.5%.
    manager.check_before_order(_order(), AccountBalance(9_000_000, 1_000_000), positions=held, pending_orders=[])
    with pytest.raises(ExposureCapError, match="theme_exposure_exceeded") as exc:
        manager.check_before_order(_order(), AccountBalance(9_000_000, 1_000_000), positions=held, pending_orders=pending)
    assert exc.value.exposure == pytest.approx(.03)


def test_verified_no_theme_allowed_but_unknown_holdings_block(db):
    _publish(db, {"AAA": [], "BBB": ["HBM"]})
    manager = _manager(db)
    manager.check_before_order(_order(), AccountBalance(10_000_000, 0))
    with pytest.raises(ExposureCapError, match="theme_classification_missing"):
        manager.check_before_order(_order(), AccountBalance(9_000_000, 1_000_000),
                                  positions={"UNKNOWN": Position("UNKNOWN", 1, 10000, current_price=10000)},
                                  pending_orders=[])


@pytest.mark.parametrize("future", [False, True])
def test_old_or_future_published_snapshot_cannot_authorize_entry(db, future):
    expected = _expected_date()
    _publish(db, {"AAA": ["AI"]}, ref_date=expected if future else expected-dt.timedelta(days=1),
             published_at=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)+dt.timedelta(days=1) if future else None)
    with pytest.raises(ExposureCapError, match="theme_classification_stale"):
        _manager(db).check_before_order(_order(), AccountBalance(10_000_000, 0))


def test_snapshot_config_loaded_from_settings():
    settings = get_settings().model_copy(update={"maps_classification_snapshot_enforced": True})
    assert RiskConfig.from_settings(settings).classification_snapshot_enforced is True


def test_theme_backtest_uses_available_snapshot_not_latest_membership(db):
    from maps.api.backtest import _resolve_universe_pool
    from maps.api.schemas import BacktestRunRequest

    _publish(db, {"AAA": ["T1"], "BBB": []}, ref_date=dt.date(2026, 10, 1),
             published_at=dt.datetime(2026, 10, 1, 9), catalog={"T1": "AI"})
    _publish(db, {"AAA": [], "BBB": ["T1"]}, ref_date=dt.date(2026, 10, 2),
             published_at=dt.datetime(2026, 10, 2, 9), catalog={"T1": "AI"})
    request = BacktestRunRequest(universe="theme", universe_arg="AI", start=dt.date(2026, 10, 2))
    pool, _ = _resolve_universe_pool(db, request)
    assert pool == ["AAA"]
    pool, _ = _resolve_universe_pool(db, request.model_copy(update={"universe_arg": "T1"}))
    assert pool == ["AAA"]


@pytest.mark.parametrize("implicit_start", [False, True])
def test_theme_backtest_rejects_snapshot_created_after_start(db, implicit_start):
    from maps.api.backtest import _resolve_universe_pool
    from maps.api.schemas import BacktestRunRequest
    from maps.common.models import HistoricalOHLCV

    _publish(db, {"AAA": ["AI"]}, ref_date=dt.date(2026, 10, 1), published_at=dt.datetime(2026, 10, 1, 9))
    db.add(HistoricalOHLCV(ticker="AAA", date=dt.date(2026, 9, 1), open=1, high=1, low=1, close=1, volume=1))
    db.commit()
    request = BacktestRunRequest(universe="theme", universe_arg="AI", start=None if implicit_start else dt.date(2026, 9, 1))
    with pytest.raises(HTTPException) as exc:
        _resolve_universe_pool(db, request)
    assert exc.value.status_code == 400
    assert "theme_snapshot_unavailable" in exc.value.detail
