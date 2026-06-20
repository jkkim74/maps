"""펀더멘털 레포·프로바이더 + 수집기 적재 테스트 (요건 5·7·8 데이터 경로)."""

from __future__ import annotations

import datetime

from maps.common.models import SecurityFundamental
from maps.data.fundamental_repo import FundamentalRepository, FundamentalValuationProvider
from maps.data.krx_adapter import FundamentalData, MockKRXAdapter
from maps.data.collector import DataCollector

REF = datetime.date(2026, 6, 19)


def _seed(db, ticker: str, date: datetime.date, *, per, pbr, eps, bps) -> None:
    db.add(SecurityFundamental(ticker=ticker, date=date, per=per, pbr=pbr, eps=eps, bps=bps))
    db.commit()


def test_get_as_of_returns_latest_on_or_before(db):
    _seed(db, "005930", datetime.date(2026, 6, 10), per=10.0, pbr=1.0, eps=5000, bps=50000)
    _seed(db, "005930", datetime.date(2026, 6, 18), per=12.0, pbr=1.2, eps=5000, bps=50000)
    _seed(db, "005930", datetime.date(2026, 6, 25), per=99.0, pbr=9.9, eps=5000, bps=50000)

    repo = FundamentalRepository(db)
    row = repo.get_as_of("005930", REF)

    assert row is not None
    assert row.per == 12.0  # 6/25 미래 행은 제외, 6/18이 최신


def test_historical_avg_ignores_future_and_none(db):
    _seed(db, "000660", datetime.date(2026, 5, 1), per=8.0, pbr=1.0, eps=1000, bps=10000)
    _seed(db, "000660", datetime.date(2026, 6, 1), per=12.0, pbr=1.0, eps=1000, bps=10000)
    _seed(db, "000660", datetime.date(2026, 7, 1), per=100.0, pbr=1.0, eps=1000, bps=10000)

    repo = FundamentalRepository(db)
    avg = repo.historical_avg("000660", REF, "per")

    assert avg == 10.0  # (8 + 12) / 2, 미래(7/1) 제외


def test_historical_band_positions_current_per(db):
    for d, per in [(datetime.date(2026, 1, 2), 5.0), (datetime.date(2026, 3, 2), 15.0)]:
        _seed(db, "035420", d, per=per, pbr=1.0, eps=1000, bps=10000)

    repo = FundamentalRepository(db)
    # 현재 PER=10 → [5,15] 밴드의 중앙(50)
    assert repo.historical_band("035420", REF, current_per=10.0) == 50.0


def test_provider_populates_valuation_input_and_roe(db):
    _seed(db, "005930", datetime.date(2026, 6, 18), per=12.0, pbr=1.2, eps=6000, bps=50000)

    provider = FundamentalValuationProvider(db, REF)
    inp = provider.get("005930", current_price=60000.0)

    assert inp.per == 12.0
    assert inp.pbr == 1.2
    assert inp.roe == 12.0  # eps/bps*100 = 6000/50000*100


def test_provider_neutral_when_no_data(db):
    provider = FundamentalValuationProvider(db, REF)
    inp = provider.get("999999", current_price=1000.0)

    assert inp.per is None and inp.pbr is None and inp.roe is None


def test_price_fundamentals_feed_value_target(db):
    # 역사적 PER 평균 15, BPS 50000 → value_target 산출 가능해야 한다
    _seed(db, "005930", datetime.date(2026, 1, 2), per=15.0, pbr=1.5, eps=3000, bps=40000)
    _seed(db, "005930", datetime.date(2026, 6, 18), per=15.0, pbr=1.5, eps=3000, bps=40000)

    from maps.strategy.price_calculator import KostolanyPriceCalculator, PriceInput

    pf = FundamentalValuationProvider(db, REF).price_fundamentals("005930")
    result = KostolanyPriceCalculator().calculate(
        PriceInput(
            holding_type="CORE",
            current_close=40000.0,
            eps_forward=pf.eps_forward,
            historical_per_avg=pf.historical_per_avg,
            bps=pf.bps,
            historical_pbr_avg=pf.historical_pbr_avg,
        )
    )

    assert pf.historical_per_avg == 15.0
    assert result.value_target is not None
    assert result.value_target > 40000.0


def test_collector_upserts_fundamentals(db):
    krx = MockKRXAdapter(seed_tickers=["005930"])
    krx.set_fundamentals({
        "005930": FundamentalData(date=REF, ticker="005930", per=11.0, pbr=1.1, eps=5000, bps=45000)
    })

    DataCollector(krx, db).collect_daily(REF)

    row = FundamentalRepository(db).get_as_of("005930", REF)
    assert row is not None
    assert row.per == 11.0
    assert row.bps == 45000


def test_collect_fundamental_history_backfills_range(db):
    krx = MockKRXAdapter(seed_tickers=["005930"])
    krx.set_fundamentals({
        "005930": FundamentalData(date=REF, ticker="005930", per=11.0, pbr=1.1, eps=5000, bps=45000)
    })

    summary = DataCollector(krx, db).collect_fundamental_history(
        datetime.date(2026, 6, 15), datetime.date(2026, 6, 19)
    )

    assert summary["success_days"] == summary["business_days"]
    assert summary["rows"] >= 1
    # 백필된 여러 날짜 → 역사적 평균 계산 가능
    assert FundamentalRepository(db).historical_avg("005930", REF, "per") == 11.0
