from __future__ import annotations

import datetime as dt

from maps.common.models import CandidateSnapshot, HistoricalOHLCV, OrderLog, PromotionHistory
from maps.common.settings import MapsSettings
from maps.execution.broker_adapter import OrderSide, OrderStatus
from maps.api.orders import get_orders
from maps.ops.order_preview import build_order_preview


def _seed_candidate(db, *, ref_date: dt.date) -> None:
    db.add(HistoricalOHLCV(
        ticker="AAAA",
        date=ref_date,
        open=10_000,
        high=10_000,
        low=10_000,
        close=10_000,
        volume=100_000,
    ))
    db.add(CandidateSnapshot(
        ref_date=ref_date,
        strategy_id="donchian_v2",
        ticker="AAAA",
        name="AAAA",
        market="KOSPI",
        factor_score=90,
        trend_strength=80,
        ts_bucket="S5",
        final_score=95,
        weekly_pass=True,
    ))
    db.add(PromotionHistory(
        strategy_id="donchian_v2",
        from_stage="research",
        to_stage="mock_candidate",
        tradeability_score=70,
        passed=True,
        evaluated_at=dt.datetime.combine(ref_date, dt.time(8)),
    ))
    db.commit()


def test_preview_hides_candidate_after_order_submission(db, monkeypatch) -> None:
    ref_date = dt.date.today() - dt.timedelta(days=1)
    _seed_candidate(db, ref_date=ref_date)
    monkeypatch.setattr("maps.ops.order_preview.next_trading_day", lambda value: value + dt.timedelta(days=1))

    before = build_order_preview(db, MapsSettings())
    assert [item.ticker for item in before.items] == ["AAAA"]

    db.add(OrderLog(
        order_id="order-1",
        strategy_id="pullback_v3",
        ticker="AAAA",
        side=OrderSide.BUY.value,
        qty=10,
        order_price=10_100,
        status=OrderStatus.PENDING.value,
        created_at=dt.datetime.now(),
    ))
    db.commit()

    after = build_order_preview(db, MapsSettings())
    assert after.data_available is True
    assert after.items == []


def test_orders_read_does_not_expire_stale_pending_row(db) -> None:
    db.add(OrderLog(
        order_id="stale-pending",
        strategy_id="pullback_v3",
        ticker="AAAA",
        side=OrderSide.BUY.value,
        qty=10,
        order_price=10_100,
        status=OrderStatus.PENDING.value,
        created_at=dt.datetime.now() - dt.timedelta(days=1),
    ))
    db.commit()

    get_orders(db)

    row = db.query(OrderLog).filter(OrderLog.order_id == "stale-pending").one()
    assert row.status == OrderStatus.PENDING.value
