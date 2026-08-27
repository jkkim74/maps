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


def test_historical_ohlcv_repository_lists_tickers_on_date() -> None:
    engine, factory = _factory()
    db = factory()
    try:
        ref_date = dt.date(2026, 5, 4)
        db.add(HistoricalOHLCV(
            ticker="000002",
            date=ref_date,
            open=20,
            high=21,
            low=19,
            close=20,
            volume=200,
            adj_close=20,
        ))
        db.add(HistoricalOHLCV(
            ticker="000001",
            date=ref_date,
            open=10,
            high=11,
            low=9,
            close=10,
            volume=100,
            adj_close=10,
        ))
        db.add(HistoricalOHLCV(
            ticker="000003",
            date=ref_date - dt.timedelta(days=1),
            open=30,
            high=31,
            low=29,
            close=30,
            volume=300,
            adj_close=30,
        ))
        db.commit()

        repo = HistoricalOHLCVRepository(db)

        assert repo.list_tickers_on_date(ref_date) == ["000001", "000002"]
        assert repo.list_tickers_on_date(ref_date, limit=1) == ["000001"]
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


def _bar(db, ticker: str, date: dt.date, close: float, volume: int) -> None:
    """테스트용 일봉 한 개."""
    db.add(
        HistoricalOHLCV(
            ticker=ticker,
            date=date,
            open=close,
            high=close,
            low=close,
            close=close,
            volume=volume,
            source="test",
        )
    )


def test_avg_turnover_20d_uses_twenty_bars_not_one(db) -> None:
    """하루만 급등한 종목이 20일 평균으로는 하한에 못 미치는 것을 잡는다.

    2026-08-20 195990 실제 사례 — 8/20 하루치 3.36억이 코스닥 하한 3억을 넘겨
    유니버스를 통과했지만 20일 평균은 3,760만이었다.
    """
    base = dt.date(2026, 7, 1)
    days = [base + dt.timedelta(days=i) for i in range(20)]
    for day in days[:19]:
        _bar(db, "195990", day, close=1000.0, volume=10_000)
    _bar(db, "195990", days[19], close=1400.0, volume=242_857)
    db.commit()

    result = HistoricalOHLCVRepository(db).avg_turnover_20d(["195990"], days[19])

    assert "195990" in result
    assert result["195990"] < 300_000_000
    assert 25_000_000 < result["195990"] < 28_000_000


def test_avg_turnover_20d_excludes_ticker_with_short_history(db) -> None:
    """봉이 20개가 안 되면 부분 평균을 주지 않고 아예 제외한다."""
    base = dt.date(2026, 7, 1)
    for i in range(19):
        _bar(db, "000001", base + dt.timedelta(days=i), close=1000.0, volume=10_000)
    db.commit()

    repo = HistoricalOHLCVRepository(db)

    assert repo.avg_turnover_20d(["000001"], base + dt.timedelta(days=18)) == {}


def test_avg_turnover_20d_ignores_bars_after_as_of(db) -> None:
    """as_of 이후 봉은 쓰지 않는다 — as-of-date 생성기 제약."""
    base = dt.date(2026, 7, 1)
    for i in range(20):
        _bar(db, "000002", base + dt.timedelta(days=i), close=1000.0, volume=10_000)
    _bar(db, "000002", base + dt.timedelta(days=20), close=1000.0, volume=10_000_000)
    db.commit()

    result = HistoricalOHLCVRepository(db).avg_turnover_20d(
        ["000002"], base + dt.timedelta(days=19)
    )

    assert result["000002"] == 10_000_000.0


def test_avg_turnover_20d_returns_empty_for_no_tickers(db) -> None:
    """티커가 없으면 쿼리하지 않는다."""
    assert HistoricalOHLCVRepository(db).avg_turnover_20d([], dt.date(2026, 7, 1)) == {}
