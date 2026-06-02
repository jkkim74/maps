from __future__ import annotations

import datetime as dt

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import maps.common.models  # noqa: F401
from maps.common.db import Base
from maps.common.models import HistoricalOHLCV
from maps.data.collector import DataCollector
from maps.data.krx_adapter import MockKRXAdapter, OHLCVData
from maps.data.ohlcv_repo import HistoricalOHLCVRepository


def _factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine, sessionmaker(autocommit=False, autoflush=False, bind=engine)


def test_collect_daily_persists_historical_ohlcv_and_upserts() -> None:
    engine, factory = _factory()
    db = factory()
    try:
        ref_date = dt.date(2026, 5, 4)
        collector = DataCollector(MockKRXAdapter(seed_tickers=["005930", "000660"]), db)

        collector.collect_daily(ref_date)
        collector.collect_daily(ref_date)

        rows = db.query(HistoricalOHLCV).order_by(HistoricalOHLCV.ticker.asc()).all()
        assert len(rows) == 2
        assert rows[0].date == ref_date
        assert rows[0].open > 0
        assert rows[0].adj_close is not None
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_historical_ohlcv_repository_returns_dataframe() -> None:
    engine, factory = _factory()
    db = factory()
    try:
        ref_date = dt.date(2026, 5, 4)
        collector = DataCollector(MockKRXAdapter(seed_tickers=["005930"]), db)
        collector.collect_daily(ref_date)

        repo = HistoricalOHLCVRepository(db)
        tickers = repo.list_tickers_with_history(min_bars=1)
        df = repo.to_dataframe("005930")

        assert tickers == ["005930"]
        assert list(df.columns) == ["open", "high", "low", "close", "volume", "adj_close"]
        assert float(df.iloc[0]["close"]) == 50_000.0
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_historical_ohlcv_repository_returns_recent_dataframes_in_batch() -> None:
    engine, factory = _factory()
    db = factory()
    try:
        start = dt.date(2026, 1, 1)
        for offset in range(5):
            day = start + dt.timedelta(days=offset)
            db.add(HistoricalOHLCV(
                ticker="000001",
                date=day,
                open=10 + offset,
                high=11 + offset,
                low=9 + offset,
                close=10 + offset,
                volume=100 + offset,
                adj_close=10 + offset,
            ))
            db.add(HistoricalOHLCV(
                ticker="000002",
                date=day,
                open=20 + offset,
                high=21 + offset,
                low=19 + offset,
                close=20 + offset,
                volume=200 + offset,
                adj_close=20 + offset,
            ))
        db.commit()

        repo = HistoricalOHLCVRepository(db)
        frames = repo.recent_dataframes(["000001", "000002"], bars=3)

        assert list(frames) == ["000001", "000002"]
        assert len(frames["000001"]) == 3
        assert float(frames["000001"].iloc[0]["close"]) == 12.0
        assert float(frames["000001"].iloc[-1]["close"]) == 14.0
        assert float(frames["000002"].iloc[-1]["close"]) == 24.0
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_collect_ohlcv_history_backfills_business_days() -> None:
    engine, factory = _factory()
    db = factory()
    try:
        collector = DataCollector(MockKRXAdapter(seed_tickers=["005930"]), db)

        result = collector.collect_ohlcv_history(
            dt.date(2026, 5, 1),
            dt.date(2026, 5, 5),
        )

        assert result["business_days"] == 3
        assert result["success_days"] == 3
        assert result["rows"] == 3
        assert db.query(HistoricalOHLCV).count() == 3
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_collect_daily_skips_invalid_zero_price_ohlcv() -> None:
    class ZeroPriceAdapter(MockKRXAdapter):
        def get_ohlcv(self, ref_date: dt.date):
            return [
                OHLCVData(
                    date=ref_date,
                    ticker="005930",
                    open=0.0,
                    high=0.0,
                    low=0.0,
                    close=0.0,
                    volume=0,
                    adj_close=None,
                )
            ]

    engine, factory = _factory()
    db = factory()
    try:
        collector = DataCollector(ZeroPriceAdapter(seed_tickers=["005930"]), db)

        collector.collect_daily(dt.date(2026, 5, 1))

        assert db.query(HistoricalOHLCV).count() == 0
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()
