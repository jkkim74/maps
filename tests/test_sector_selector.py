"""tests/test_sector_selector.py — SectorSelector 단위 테스트."""

from __future__ import annotations

import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from maps.common.models import Base, HistoricalOHLCV, SecurityMetadata
from maps.market.regime import RegimeLabel, RegimeResult, VolRegimeLabel, WeeklyTrendLabel
from maps.market.sector_selector import SectorSelector


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    yield session
    session.close()
    engine.dispose()


def _add_stock(db: Session, ticker: str, name: str, sector: str | None) -> None:
    db.add(
        SecurityMetadata(
            ticker=ticker,
            name=name,
            market="KOSPI",
            security_type="STOCK",
            has_adjusted_price=False,
            sector=sector,
        )
    )


def _add_ohlcv(db: Session, ticker: str, date: datetime.date, close: float) -> None:
    db.add(
        HistoricalOHLCV(
            ticker=ticker,
            date=date,
            open=close,
            high=close,
            low=close,
            close=close,
            volume=100_000,
            adj_close=close,
        )
    )


def _make_regime(label: str = "strong") -> RegimeResult:
    return RegimeResult(
        regime=RegimeLabel(label),
        weekly_trend=WeeklyTrendLabel.PASS,
        limit_ratio=1.0,
        kospi_ts=None,
        vol_regime=VolRegimeLabel.NORMAL,
        assets=[],
    )


REF_DATE = datetime.date(2024, 6, 1)
START_DATE = datetime.date(2024, 5, 1)


class TestSectorSelectorBasic:
    def test_returns_empty_when_no_data(self, db: Session) -> None:
        selector = SectorSelector(lookback_days=20, top_n=5)
        result = selector.select_strong_sectors(db, REF_DATE, _make_regime())
        assert result == []

    def test_selects_top_n_sectors(self, db: Session) -> None:
        # IT: 30→60 수익률 100%, 헬스케어: 30→33 수익률 10%
        for i, ticker in enumerate(["A", "B", "C"]):
            _add_stock(db, ticker, f"IT종목{i}", "정보기술")
            _add_ohlcv(db, ticker, START_DATE, 30_000)
            _add_ohlcv(db, ticker, REF_DATE, 60_000)

        for i, ticker in enumerate(["D", "E", "F"]):
            _add_stock(db, ticker, f"헬스케어종목{i}", "헬스케어")
            _add_ohlcv(db, ticker, START_DATE, 30_000)
            _add_ohlcv(db, ticker, REF_DATE, 33_000)

        db.commit()

        selector = SectorSelector(lookback_days=40, top_n=1)
        result = selector.select_strong_sectors(db, REF_DATE, _make_regime("strong"))
        assert "정보기술" in result
        assert len(result) == 1

    def test_top_n_limits_result(self, db: Session) -> None:
        sectors = ["정보기술", "헬스케어", "금융", "에너지", "소재"]
        for idx, sector in enumerate(sectors):
            for j in range(3):
                ticker = f"{idx:02d}{j}"
                _add_stock(db, ticker, f"종목{ticker}", sector)
                _add_ohlcv(db, ticker, START_DATE, 10_000)
                _add_ohlcv(db, ticker, REF_DATE, 10_000 + idx * 1_000)
        db.commit()

        selector = SectorSelector(lookback_days=40, top_n=3)
        result = selector.select_strong_sectors(db, REF_DATE, _make_regime())
        assert len(result) <= 3

    def test_weak_regime_prefers_defensive_sectors(self, db: Session) -> None:
        # IT 수익률 고, 헬스케어(방어) 수익률 중간
        for ticker in ["A", "B", "C"]:
            _add_stock(db, ticker, f"IT{ticker}", "정보기술")
            _add_ohlcv(db, ticker, START_DATE, 10_000)
            _add_ohlcv(db, ticker, REF_DATE, 20_000)  # +100%

        for ticker in ["D", "E", "F"]:
            _add_stock(db, ticker, f"헬스{ticker}", "헬스케어")
            _add_ohlcv(db, ticker, START_DATE, 10_000)
            _add_ohlcv(db, ticker, REF_DATE, 11_000)  # +10%

        db.commit()

        selector = SectorSelector(lookback_days=40, top_n=2)
        result = selector.select_strong_sectors(db, REF_DATE, _make_regime("weak"))
        # WEAK 시황에서 헬스케어(방어업종)가 목록에 포함되어야 함
        assert "헬스케어" in result

    def test_excludes_sector_with_fewer_than_3_tickers(self, db: Session) -> None:
        # 2개 종목만 있는 업종은 제외
        for ticker in ["A", "B"]:
            _add_stock(db, ticker, f"소규모{ticker}", "특수업종")
            _add_ohlcv(db, ticker, START_DATE, 10_000)
            _add_ohlcv(db, ticker, REF_DATE, 20_000)
        db.commit()

        selector = SectorSelector(lookback_days=40, top_n=5)
        result = selector.select_strong_sectors(db, REF_DATE, _make_regime())
        assert "특수업종" not in result

    def test_null_sector_tickers_ignored(self, db: Session) -> None:
        for ticker in ["A", "B", "C"]:
            _add_stock(db, ticker, f"무업종{ticker}", None)  # sector=None
            _add_ohlcv(db, ticker, START_DATE, 10_000)
            _add_ohlcv(db, ticker, REF_DATE, 20_000)
        db.commit()

        selector = SectorSelector(lookback_days=40, top_n=5)
        result = selector.select_strong_sectors(db, REF_DATE, _make_regime())
        assert result == []


class TestSectorSelectorMockAdapter:
    def test_mock_adapter_set_sectors(self) -> None:
        from maps.data.krx_adapter import MockKRXAdapter

        adapter = MockKRXAdapter(seed_tickers=["005930", "000660"])
        adapter.set_sectors({"005930": "정보기술", "000660": "정보기술"})
        result = adapter.get_sector_classifications(REF_DATE)
        assert result["005930"] == "정보기술"
        assert result["000660"] == "정보기술"

    def test_mock_adapter_default_empty(self) -> None:
        from maps.data.krx_adapter import MockKRXAdapter

        adapter = MockKRXAdapter()
        result = adapter.get_sector_classifications(REF_DATE)
        assert result == {}
