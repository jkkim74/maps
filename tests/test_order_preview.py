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
    # 신선도 가드를 통과하도록 오늘 날짜로 시드한다(직전 거래일 기준 stale 아님).
    ref_date = dt.date.today()
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


def test_preview_marks_stale_when_candidates_old(db, monkeypatch) -> None:
    stale_date = dt.date.today() - dt.timedelta(days=30)
    _seed_candidate(db, ref_date=stale_date)
    monkeypatch.setattr("maps.ops.order_preview.next_trading_day", lambda value: value + dt.timedelta(days=1))

    resp = build_order_preview(db, MapsSettings())

    assert resp.data_stale is True
    assert resp.items == []
    assert resp.expected_ref_date is not None
    # 마지막으로 생성된(오래된) 기준일을 투명하게 노출한다.
    assert resp.as_of_date == stale_date.isoformat()
    # OHLCV도 함께 오래됐으므로 수집 실패로 판별한다.
    assert resp.stale_reason == "data_collection_failed"
    assert resp.latest_ohlcv_date == stale_date.isoformat()


def test_preview_stale_reason_regime_blocked_when_ohlcv_fresh(db, monkeypatch) -> None:
    stale_date = dt.date.today() - dt.timedelta(days=30)
    _seed_candidate(db, ref_date=stale_date)
    # 수집은 정상: 최신 OHLCV가 존재하지만 후보 스냅샷만 생성되지 않은 상황
    db.add(HistoricalOHLCV(
        ticker="BBBB",
        date=dt.date.today(),
        open=10_000,
        high=10_000,
        low=10_000,
        close=10_000,
        volume=100_000,
    ))
    db.commit()
    monkeypatch.setattr("maps.ops.order_preview.next_trading_day", lambda value: value + dt.timedelta(days=1))

    resp = build_order_preview(db, MapsSettings())

    assert resp.data_stale is True
    assert resp.stale_reason == "regime_blocked"
    assert resp.latest_ohlcv_date == dt.date.today().isoformat()


def test_preview_fresh_when_candidate_current(db, monkeypatch) -> None:
    _seed_candidate(db, ref_date=dt.date.today())
    monkeypatch.setattr("maps.ops.order_preview.next_trading_day", lambda value: value + dt.timedelta(days=1))

    resp = build_order_preview(db, MapsSettings())

    assert resp.data_stale is False
    assert resp.stale_reason is None
    assert [item.ticker for item in resp.items] == ["AAAA"]


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
