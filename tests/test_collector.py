"""``DataCollector`` 종목 메타 upsert 계약 — 상장일을 잃지 않고, 결측을 알린다.

2026-09-07: 운영 ``security_metadata.listing_date`` 가 전부 NULL 이었고 그 사실을
아무도 몰랐다. 상한가 V1 자격 판정은 상장일 NULL 을 fail-closed 로 막으므로 엔진이
3주간 후보를 한 건도 수락하지 못했다.
"""

from __future__ import annotations

import datetime

from maps.common.models import SecurityMetadata
from maps.data.collector import DataCollector
from maps.data.krx_adapter import MockKRXAdapter, SecurityMeta


def _meta(ticker: str, listing_date: datetime.date | None, name: str = "종목") -> SecurityMeta:
    return SecurityMeta(
        ticker=ticker,
        name=name,
        market="KOSDAQ",
        security_type="STOCK",
        listing_date=listing_date,
        delisting_date=None,
    )


def _existing(db, ticker: str, listing_date: datetime.date | None) -> None:
    db.add(
        SecurityMetadata(
            ticker=ticker,
            name="기존",
            market="KOSDAQ",
            security_type="STOCK",
            listing_date=listing_date,
        )
    )
    db.commit()


def test_upsert_meta_keeps_a_known_listing_date_when_the_source_has_none(db) -> None:
    """KRX 상장일 조회가 실패한 날에도 전날 알던 값이 NULL 로 되돌아가지 않는다."""
    _existing(db, "005930", datetime.date(1975, 6, 11))
    collector = DataCollector(MockKRXAdapter(), db)

    collector._upsert_meta([_meta("005930", None)])

    row = db.query(SecurityMetadata).filter_by(ticker="005930").one()
    assert row.listing_date == datetime.date(1975, 6, 11)


def test_upsert_meta_updates_a_listing_date_when_the_source_knows_it(db) -> None:
    _existing(db, "005930", None)
    collector = DataCollector(MockKRXAdapter(), db)

    collector._upsert_meta([_meta("005930", datetime.date(1975, 6, 11))])

    row = db.query(SecurityMetadata).filter_by(ticker="005930").one()
    assert row.listing_date == datetime.date(1975, 6, 11)


def test_upsert_meta_warns_when_most_stocks_have_no_listing_date(db, caplog) -> None:
    """결측이 절반을 넘으면 WARNING — 이번 사고를 하루 만에 드러냈을 신호다."""
    collector = DataCollector(MockKRXAdapter(), db)
    metas = [_meta(f"00000{i}", None) for i in range(3)] + [_meta("005930", datetime.date(1975, 6, 11))]

    with caplog.at_level("INFO", logger="maps.data.collector"):
        collector._upsert_meta(metas)

    warnings = [r for r in caplog.records if r.levelname == "WARNING" and "상장일 결측" in r.getMessage()]
    assert len(warnings) == 1
    assert "3/4" in warnings[0].getMessage()


def test_upsert_meta_stays_quiet_when_only_a_few_listing_dates_are_missing(db, caplog) -> None:
    """상폐 추정 종목 몇 건은 매일 NULL 로 남는다 — 그걸로 매일 울리면 신호가 죽는다."""
    collector = DataCollector(MockKRXAdapter(), db)
    metas = [_meta(f"00000{i}", datetime.date(2000, 1, 1)) for i in range(3)] + [_meta("999999", None)]

    with caplog.at_level("INFO", logger="maps.data.collector"):
        collector._upsert_meta(metas)

    assert not [r for r in caplog.records if r.levelname == "WARNING" and "상장일 결측" in r.getMessage()]
    assert any("상장일 결측 1/4" in r.getMessage() for r in caplog.records)


def test_metadata_failure_keeps_prices_and_persists_quality(db, monkeypatch):
    from maps.common.models import CollectionLog, HistoricalOHLCV
    import maps.market.feeds as feeds
    monkeypatch.setattr(feeds, "collect_market_news_sentiment", lambda *args: None)
    adapter = MockKRXAdapter()
    def broken(day):
        raise RuntimeError("metadata unavailable")
    monkeypatch.setattr(adapter, "get_security_meta", broken)
    day = datetime.date(2026, 9, 30)
    result = DataCollector(adapter, db).collect_daily(day)
    assert db.query(HistoricalOHLCV).filter_by(date=day).count() == 3
    assert result.metadata_quality["status"] == "unavailable"
    assert result.metadata_quality["candidate_ready"] is False
    log = db.query(CollectionLog).one()
    assert log.status == "partial"
    assert log.metadata_quality == result.metadata_quality


def test_metadata_partial_log_remains_after_successful_retry(db, monkeypatch):
    from maps.common.models import CollectionLog
    from maps.data.krx_adapter import InvestorFlowData
    import maps.market.feeds as feeds
    monkeypatch.setattr(feeds, "collect_market_news_sentiment", lambda *args: None)
    adapter = MockKRXAdapter()
    day = datetime.date(2026, 9, 30)
    adapter.set_investor_flows({"005930": InvestorFlowData(day, "005930", "KOSPI", 1, 1, 1)})
    original = adapter.get_security_meta
    monkeypatch.setattr(adapter, "get_security_meta", lambda day: [])
    collector = DataCollector(adapter, db)
    collector.collect_daily(day)
    monkeypatch.setattr(adapter, "get_security_meta", original)
    collector.collect_daily(day)
    logs = db.query(CollectionLog).order_by(CollectionLog.id).all()
    assert [log.status for log in logs] == ["partial", "success"]
    assert logs[0].metadata_quality["status"] == "unavailable"
    assert logs[1].metadata_quality["candidate_ready"] is True


def test_failed_daily_log_keeps_observed_metadata_quality(db, monkeypatch):
    import pytest
    from maps.common.exceptions import DataCollectionError
    from maps.common.models import CollectionLog
    adapter = MockKRXAdapter()
    def broken(day):
        raise RuntimeError("halt failure")
    monkeypatch.setattr(adapter, "get_halt_list", broken)
    with pytest.raises(DataCollectionError):
        DataCollector(adapter, db).collect_daily(datetime.date(2026, 9, 30))
    log = db.query(CollectionLog).one()
    assert log.status == "failed"
    assert log.metadata_quality["status"] == "complete"
    assert log.metadata_quality["markets"]["KOSPI"]["valid_count"] == 3
