"""주문 정산 리포트(build_reconciliation) 테스트."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import maps.common.models  # noqa: F401 — 모델 등록
from maps.common.db import Base
from maps.common.models import HistoricalOHLCV, OrderLog
from maps.ops.reconciliation import build_reconciliation, format_reconciliation_text


def _factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine)


def _created_at(kst_day: dt.date) -> dt.datetime:
    # UTC 01:00 → +9h = KST 10:00 같은 날 → _kst_date == kst_day
    return dt.datetime.combine(kst_day, dt.time(1, 0))


def _add_order(db, *, day, side, status, ticker, order_price, qty=10):
    db.add(OrderLog(
        order_id=f"{ticker}-{side}-{status}",
        strategy_id="pullback_v3",
        ticker=ticker,
        side=side,
        qty=qty,
        order_price=order_price,
        fill_price=order_price if status == "filled" else None,
        fill_qty=qty if status == "filled" else 0,
        status=status,
        broker="kis",
        mode="live",
        created_at=_created_at(day),
    ))


def _add_ohlcv(db, *, day, ticker, low, high, close):
    db.add(HistoricalOHLCV(
        ticker=ticker, date=day,
        open=close, high=high, low=low, close=close, volume=1000,
    ))


def test_reconciliation_aggregates_and_diagnoses() -> None:
    engine, factory = _factory()
    db = factory()
    day = dt.date(2026, 6, 15)
    try:
        # 매수: 1 체결 + 1 만료(지정가 1000, 당일 저가 950 → 도달가능)
        _add_order(db, day=day, side="buy", status="filled", ticker="000001", order_price=1000)
        _add_order(db, day=day, side="buy", status="expired", ticker="000002", order_price=1000)
        _add_ohlcv(db, day=day, ticker="000002", low=950, high=1050, close=1000)
        # 매도: 1 만료(지정가 2000, 당일 고가 1800 → 도달불가) + 1 만료(지정가 None → 판정불가)
        _add_order(db, day=day, side="sell", status="expired", ticker="000003", order_price=2000)
        _add_ohlcv(db, day=day, ticker="000003", low=1700, high=1800, close=1750)
        _add_order(db, day=day, side="sell", status="expired", ticker="000004", order_price=None)
        db.commit()

        summary = build_reconciliation(db, days=30, end=dt.date(2026, 6, 16))

        assert summary.total_orders == 4
        buy = next(s for s in summary.by_side if s.side == "buy")
        sell = next(s for s in summary.by_side if s.side == "sell")
        assert buy.submitted == 2 and buy.filled == 1 and buy.expired == 1
        assert buy.fill_rate == 0.5
        assert sell.submitted == 2 and sell.expired == 2 and sell.filled == 0
        assert sell.fill_rate == 0.0

        # 미체결 3건 (buy 1 + sell 2)
        assert len(summary.unfilled) == 3
        by_ticker = {u.ticker: u for u in summary.unfilled}
        assert by_ticker["000002"].reachable is True    # 매수 저가 950 <= 1000
        assert by_ticker["000003"].reachable is False   # 매도 고가 1800 < 2000
        assert by_ticker["000004"].reachable is None     # 지정가 없음

        text = format_reconciliation_text(summary)
        assert "정산 리포트" in text
        assert "도달가능" in text
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_reconciliation_empty() -> None:
    engine, factory = _factory()
    db = factory()
    try:
        summary = build_reconciliation(db, days=7)
        assert summary.total_orders == 0
        assert summary.unfilled == []
        # 빈 결과도 텍스트 포맷이 깨지지 않아야 한다
        assert "정산 리포트" in format_reconciliation_text(summary)
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()
