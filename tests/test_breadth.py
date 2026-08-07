from __future__ import annotations

import datetime as dt

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import maps.common.models  # noqa: F401
from maps.market import breadth
from maps.common.db import Base
from maps.common.models import HistoricalOHLCV
from maps.market.breadth import classify_breadth, compute_pct_above_ma
from maps.market.regime import BreadthLabel


def _factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine, sessionmaker(autocommit=False, autoflush=False, bind=engine)


def _add_series(db, ticker: str, closes: list[float], end: dt.date) -> None:
    n = len(closes)
    for i, close in enumerate(closes):
        day = end - dt.timedelta(days=(n - 1 - i))
        db.add(HistoricalOHLCV(
            ticker=ticker,
            date=day,
            open=close,
            high=close,
            low=close,
            close=close,
            volume=100,
            adj_close=close,
        ))


def test_compute_pct_above_ma_counts_up_tickers() -> None:
    engine, factory = _factory()
    db = factory()
    try:
        ref = dt.date(2026, 5, 4)
        # 3 종목 상승(마지막 종가 > 이동평균), 1 종목 하락
        _add_series(db, "000001", [10, 11, 12, 13, 20], ref)
        _add_series(db, "000002", [10, 11, 12, 13, 20], ref)
        _add_series(db, "000003", [10, 11, 12, 13, 20], ref)
        _add_series(db, "000004", [20, 18, 16, 14, 10], ref)
        db.commit()

        pct = compute_pct_above_ma(db, ref, ma_window=5, min_tickers=2)
        assert pct == 0.75
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_compute_pct_above_ma_excludes_tickers_with_insufficient_bars() -> None:
    engine, factory = _factory()
    db = factory()
    try:
        ref = dt.date(2026, 5, 4)
        _add_series(db, "000001", [10, 11, 12, 13, 20], ref)  # up, 5 bars
        _add_series(db, "000002", [10, 11, 12, 13, 20], ref)  # up, 5 bars
        _add_series(db, "000003", [9, 10, 8], ref)            # only 3 bars → 제외
        db.commit()

        # 유효 종목 2개(모두 상승) → 1.0, 짧은 종목은 분모에서 제외
        pct = compute_pct_above_ma(db, ref, ma_window=5, min_tickers=2)
        assert pct == 1.0
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_compute_pct_above_ma_returns_none_below_min_tickers() -> None:
    engine, factory = _factory()
    db = factory()
    try:
        ref = dt.date(2026, 5, 4)
        _add_series(db, "000001", [10, 11, 12, 13, 20], ref)
        db.commit()

        assert compute_pct_above_ma(db, ref, ma_window=5, min_tickers=2) is None
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_compute_pct_above_ma_limits_bulk_query_history(monkeypatch) -> None:
    """시장폭 조회가 종목 전체 이력을 정렬하지 않도록 시작일을 제한한다."""
    ref_date = dt.date(2026, 8, 7)
    calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        breadth.HistoricalOHLCVRepository,
        "list_tickers_on_date",
        lambda self, target: ["000001", "000002"],
    )

    def recent_dataframes(self, tickers, **kwargs):
        calls.append({"tickers": tickers, **kwargs})
        dates = pd.date_range("2026-08-06", periods=2)
        return {
            "000001": pd.DataFrame({"close": [100.0, 110.0]}, index=dates),
            "000002": pd.DataFrame({"close": [100.0, 90.0]}, index=dates),
        }

    monkeypatch.setattr(
        breadth.HistoricalOHLCVRepository,
        "recent_dataframes",
        recent_dataframes,
    )

    result = breadth.compute_pct_above_ma(
        object(),
        ref_date,
        ma_window=2,
        min_tickers=2,
    )

    assert result == 0.5
    assert calls == [
        {
            "tickers": ["000001", "000002"],
            "start": ref_date - dt.timedelta(days=16),
            "end": ref_date,
            "bars": 2,
        }
    ]


def test_classify_breadth_boundaries() -> None:
    assert classify_breadth(None) == BreadthLabel.UNKNOWN
    assert classify_breadth(0.39, weak_threshold=0.40) == BreadthLabel.WEAK
    assert classify_breadth(0.40, weak_threshold=0.40) == BreadthLabel.NORMAL
    assert classify_breadth(0.55) == BreadthLabel.NORMAL
    assert classify_breadth(0.60) == BreadthLabel.STRONG
    assert classify_breadth(0.80) == BreadthLabel.STRONG
