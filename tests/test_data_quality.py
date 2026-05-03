"""DataQualityFilter as-of-date 생성기 테스트 (Phase 1 완료 기준)."""

from __future__ import annotations

import datetime
from unittest.mock import MagicMock

import pytest

from maps.data.security_repo import HaltPeriod, ManagedPeriod, Security
from maps.data_quality.universe_filter import (
    MIN_LISTING_DAYS,
    MIN_TURNOVER_KRW,
    DataQualityFilter,
)

REF_DATE = datetime.date(2024, 6, 1)


def _make_db():
    """universe_quality_log insert를 무시하는 Mock DB."""
    db = MagicMock()
    db.add = MagicMock()
    db.commit = MagicMock()
    db.rollback = MagicMock()
    return db


def _stock(
    ticker: str = "000001",
    market: str = "KOSPI",
    security_type: str = "STOCK",
    listing_date: datetime.date | None = None,
    delisting_date: datetime.date | None = None,
    has_adjusted_price: bool = True,
    halt_periods: list | None = None,
    managed_periods: list | None = None,
    turnover: float = 1e9,
) -> Security:
    ld = listing_date or datetime.date(2000, 1, 1)
    s = Security(
        ticker=ticker,
        name=f"종목_{ticker}",
        market=market,
        security_type=security_type,
        listing_date=ld,
        delisting_date=delisting_date,
        has_adjusted_price=has_adjusted_price,
        halt_periods=halt_periods or [],
        managed_periods=managed_periods or [],
    )
    s.turnover_cache[REF_DATE] = turnover
    return s


# ---------------------------------------------------------------------------
# 1. ref_date 이후 폐지 종목은 포함
# ---------------------------------------------------------------------------
def test_generate_as_of_date_excludes_future_delisted():
    """backtest 모드: ref_date 이후 폐지는 포함, 이전 폐지는 제외."""
    future_delisted = _stock("A", delisting_date=datetime.date(2024, 12, 31))
    past_delisted = _stock("B", delisting_date=datetime.date(2024, 5, 31))
    normal = _stock("C")

    flt = DataQualityFilter(db=_make_db(), mode="backtest")
    result = flt.generate(REF_DATE, [future_delisted, past_delisted, normal])

    tickers = [s.ticker for s in result.universe]
    assert "A" in tickers       # ref_date 이후 폐지 → 포함
    assert "B" not in tickers   # ref_date 이전 폐지 → 제외
    assert "C" in tickers


# ---------------------------------------------------------------------------
# 2. backtest 모드: 폐지일 당일까지 포함
# ---------------------------------------------------------------------------
def test_backtest_include_delisted_until_delisting_date():
    """backtest 모드: delisting_date == ref_date 이면 포함."""
    same_day = _stock("A", delisting_date=REF_DATE)
    day_before = _stock("B", delisting_date=REF_DATE - datetime.timedelta(days=1))

    flt = DataQualityFilter(db=_make_db(), mode="backtest")
    result = flt.generate(REF_DATE, [same_day, day_before])

    assert "A" in [s.ticker for s in result.universe]
    assert "B" not in [s.ticker for s in result.universe]


# ---------------------------------------------------------------------------
# 3. listing_date > ref_date → 제외 (미래 상장)
# ---------------------------------------------------------------------------
def test_no_lookahead_future_listing():
    """listing_date 가 ref_date 이후인 종목은 제외한다."""
    not_yet = _stock("A", listing_date=REF_DATE + datetime.timedelta(days=1))
    flt = DataQualityFilter(db=_make_db(), mode="backtest")
    result = flt.generate(REF_DATE, [not_yet])

    assert len(result.universe) == 0
    assert result.rejected[0]["reason"] == "not_listed_yet"


# ---------------------------------------------------------------------------
# 4. 신규상장 100일 미만 제외
# ---------------------------------------------------------------------------
def test_recently_listed_excluded():
    """상장 후 MIN_LISTING_DAYS 미만이면 제외."""
    new_stock = _stock(
        "A",
        listing_date=REF_DATE - datetime.timedelta(days=MIN_LISTING_DAYS - 1),
    )
    flt = DataQualityFilter(db=_make_db(), mode="backtest")
    result = flt.generate(REF_DATE, [new_stock])

    assert len(result.universe) == 0
    assert result.rejected[0]["reason"] == "recently_listed"


# ---------------------------------------------------------------------------
# 5. KOSPI 거래대금 5억 미만 제외
# ---------------------------------------------------------------------------
def test_low_turnover_kospi_excluded():
    """KOSPI 종목의 20일 평균 거래대금이 5억 미만이면 제외."""
    low_vol = _stock("A", market="KOSPI", turnover=MIN_TURNOVER_KRW["KOSPI"] - 1)
    sufficient = _stock("B", market="KOSPI", turnover=MIN_TURNOVER_KRW["KOSPI"])

    flt = DataQualityFilter(db=_make_db(), mode="backtest")
    result = flt.generate(REF_DATE, [low_vol, sufficient])

    rejected_tickers = [r["ticker"] for r in result.rejected]
    assert "A" in rejected_tickers
    assert "B" not in rejected_tickers


# ---------------------------------------------------------------------------
# 6. 거부율 5% 초과 시 알림 함수 호출
# ---------------------------------------------------------------------------
def test_rejection_alert_called_when_ratio_exceeds_threshold():
    """거부율 >= 5% 이면 alert_fn 이 호출된다."""
    alerts: list[str] = []

    # 100명 중 6명 탈락 → 6% > 5%
    candidates = [_stock(str(i)) for i in range(94)]
    # 상장일을 미래로 설정해 6명 제외
    excluded = [
        _stock(str(100 + i), listing_date=REF_DATE + datetime.timedelta(days=1))
        for i in range(6)
    ]

    flt = DataQualityFilter(
        db=_make_db(),
        mode="backtest",
        alert_fn=lambda msg: alerts.append(msg),
    )
    flt.generate(REF_DATE, candidates + excluded)

    assert len(alerts) == 1
    assert "[DQ]" in alerts[0]
