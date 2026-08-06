"""브로커 계좌 교체 이후 성과 이력 경계 테스트."""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy.orm import Session

from maps.common.account_history import account_history_start_utc_naive, clamp_history_start_date
from maps.common.models import OrderLog
from maps.common.settings import MapsSettings
from maps.ops.scheduler import OperationalPipeline


def test_account_start_converts_kst_midnight_to_utc_naive() -> None:
    """KST 8/5 자정은 DB created_at 기준 UTC 8/4 15시다."""
    settings = MapsSettings(maps_account_history_start_date=dt.date(2026, 8, 5))

    assert account_history_start_utc_naive(settings) == dt.datetime(2026, 8, 4, 15, 0)
    assert clamp_history_start_date(dt.date(2026, 1, 1), settings) == dt.date(2026, 8, 5)


def test_mock_track_months_ignores_fills_before_current_account(db: Session) -> None:
    """구 계좌의 002810 체결은 새 계좌 mock_months에 포함하지 않는다."""
    settings = MapsSettings(maps_account_history_start_date=dt.date(2026, 8, 5))
    pipeline = OperationalPipeline(settings=settings)
    db.add_all([
        OrderLog(
            order_id="old-002810",
            strategy_id="multi_asset_trend_v1",
            ticker="002810",
            side="buy",
            qty=226,
            fill_price=45_000,
            fill_qty=226,
            status="filled",
            created_at=dt.datetime(2026, 7, 28, 23, 55),
        ),
        OrderLog(
            order_id="new-account-fill",
            strategy_id="multi_asset_trend_v1",
            ticker="005930",
            side="buy",
            qty=1,
            fill_price=70_000,
            fill_qty=1,
            status="filled",
            created_at=dt.datetime(2026, 8, 5, 23, 55),
        ),
    ])
    db.commit()

    months = pipeline._mock_track_months(db, dt.date(2026, 9, 6), settings)

    assert months["multi_asset_trend_v1"] == pytest.approx(31 / 30.44)
