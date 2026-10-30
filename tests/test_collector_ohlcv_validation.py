"""Malformed provider bars must never reach stored or returned trading inputs."""

from __future__ import annotations

import datetime as dt
from dataclasses import replace

import pytest

from maps.common.exceptions import DataCollectionError
from maps.common.models import CollectionLog, HistoricalOHLCV
from maps.common.settings import MapsSettings
from maps.data.collector import DataCollector
from maps.data.krx_adapter import InvestorFlowData, MockKRXAdapter, OHLCVData
from maps.ops.scheduler import OperationalPipeline


@pytest.fixture
def bar() -> OHLCVData:
    """A valid flat bar also covers zero-volume and absent-adjustment boundaries."""
    return OHLCVData(dt.date(2026, 9, 21), "095570", 4185, 4185, 4185, 4185, 0)


@pytest.mark.parametrize("changes", [
    {"high": 4180},  # Production 095570: close 4185 > high 4180.
    {"open": 4180},  # Positive low above open.
    {"high": 4100, "low": 4200},
    *[{field: value} for field in ("open", "high", "low", "close")
      for value in (None, 0, -1, float("nan"), float("inf"), float("-inf"))],
    *[{"volume": value} for value in (None, -1, float("nan"), float("inf"))],
    *[{"adj_close": value} for value in (0, -1, float("nan"), float("inf"))],
], ids=str)
def test_invalid_bar_cannot_overwrite_existing_good_row(db, bar, changes, caplog) -> None:
    """Removing any boundary guard would allow corrupted updates or lose their audit."""
    collector = DataCollector(MockKRXAdapter(), db)
    assert collector._upsert_ohlcv([bar]) == 1

    assert collector._upsert_ohlcv([replace(bar, **changes)]) == 0

    stored = db.query(HistoricalOHLCV).one()
    assert (stored.open, stored.high, stored.low, stored.close, stored.volume, stored.adj_close) == (
        4185, 4185, 4185, 4185, 0, None,
    )
    assert "095570" in caplog.text
    assert "2026-09-21" in caplog.text
    assert "reason=" in caplog.text


def test_collect_daily_filters_bad_bars_and_audits_partial_collection(db, bar, monkeypatch) -> None:
    """Bad prices must not leak through CollectionResult or masquerade as a complete day."""
    adapter = MockKRXAdapter(seed_tickers=["095570", "005930"])
    adapter.set_sectors({"095570": "Services", "005930": "Tech"})
    adapter.set_investor_flows({"005930": InvestorFlowData(bar.date, "005930", "KOSPI", 1, 1, 1)})
    monkeypatch.setattr(adapter, "get_ohlcv", lambda day: [
        replace(bar, high=4180), replace(bar, ticker="005930", adj_close=4185),
    ])
    monkeypatch.setattr("maps.market.feeds.collect_market_news_sentiment", lambda *args: None)

    result = DataCollector(adapter, db).collect_daily(bar.date)

    assert [row.ticker for row in result.ohlcv] == ["005930"]
    assert result.ohlcv_quality["rejected_count"] == 1
    assert result.ohlcv_quality["rejected_rows"][0]["ticker"] == "095570"
    assert [row.ticker for row in db.query(HistoricalOHLCV).all()] == ["005930"]
    log = db.query(CollectionLog).one()
    assert log.status == "partial"
    assert log.items == 1
    assert "ohlcv_rejected_rows=1" in log.note
    assert "095570" in log.note and "2026-09-21" in log.note and "range" in log.note


def test_history_reports_partial_day_and_continues_after_invalid_bar(db, bar, monkeypatch) -> None:
    """A partly saved day is distinguishable from a complete or wholly failed fetch."""
    adapter = MockKRXAdapter()
    monkeypatch.setattr(adapter, "get_ohlcv", lambda day: [
        replace(bar, date=day, high=4180) if day == bar.date else replace(bar, date=day),
        replace(bar, date=day, ticker="005930"),
    ])

    result = DataCollector(adapter, db).collect_ohlcv_history(bar.date, dt.date(2026, 9, 22))

    assert result["success_days"] == 1
    assert result["partial_days"] == 1
    assert result["failed_days"] == 0
    assert result["rows"] == 3
    assert result["rejected_rows"] == 1
    logs = db.query(CollectionLog).order_by(CollectionLog.id).all()
    assert [(log.status, log.items) for log in logs] == [("partial", 1), ("success", 2)]
    assert "095570" in logs[0].note and "2026-09-21" in logs[0].note and "range" in logs[0].note


def test_valid_flat_bar_with_zero_volume_is_saved(db, bar) -> None:
    """Closed ranges and zero volume are valid, even without an adjusted close."""
    assert DataCollector(MockKRXAdapter(), db)._upsert_ohlcv([bar]) == 1
    stored = db.query(HistoricalOHLCV).one()
    assert stored.high == stored.low == stored.open == stored.close == 4185
    assert stored.volume == 0
    assert stored.adj_close is None


@pytest.mark.parametrize("malformed, expected_status", [(True, "partial"), (False, "success")])
def test_collection_job_exposes_price_rejections_without_masking_good_days(
    db, bar, monkeypatch, malformed, expected_status,
) -> None:
    """Operational status and details must reflect prices even with all other data complete."""
    adapter = MockKRXAdapter(seed_tickers=["095570"])
    adapter.set_sectors({"095570": "Services"})
    adapter.set_investor_flows({"095570": InvestorFlowData(bar.date, "095570", "KOSPI", 1, 1, 1)})
    monkeypatch.setattr(adapter, "get_ohlcv", lambda day: [replace(bar, high=4180) if malformed else bar])
    monkeypatch.setattr("maps.market.feeds.collect_market_news_sentiment", lambda *args: None)
    pipeline = OperationalPipeline(
        settings=MapsSettings(maps_data_provider="mock", maps_fujimoto_enabled=False),
        session_factory=lambda: db,
    )
    monkeypatch.setattr(pipeline, "_make_krx_adapter", lambda: adapter)

    run = pipeline.collect_data(bar.date)

    assert run.status == expected_status
    quality = run.details["ohlcv_quality"]
    assert quality["rejected_count"] == (1 if malformed else 0)
    if malformed:
        assert quality["rejected_rows"][0]["ticker"] == "095570"
        assert quality["rejected_rows"][0]["date"] == "2026-09-21"
        assert "range" in quality["rejected_rows"][0]["reason"]


@pytest.mark.parametrize("days, expected_status, expected_rows, partial_days, failed_days", [
    (["invalid"], "failed", 0, 1, 0),
    (["mixed"], "partial", 1, 1, 0),
    (["invalid", "valid"], "partial", 1, 1, 0),
    (["unavailable"], "failed", 0, 0, 1),
    (["unavailable", "valid"], "partial", 1, 0, 1),
    (["valid"], "success", 1, 0, 0),
])
def test_history_job_status_matches_saved_and_incomplete_days(
    db, bar, monkeypatch, days, expected_status, expected_rows, partial_days, failed_days,
) -> None:
    """A zero-save rejected/failed backfill must never report operational success."""
    adapter = MockKRXAdapter()

    def get_ohlcv(day: dt.date) -> list[OHLCVData]:
        state = days[(day - bar.date).days]
        if state == "unavailable":
            raise DataCollectionError("source unavailable")
        good = replace(bar, date=day)
        bad = replace(good, high=4180, ticker="005930")
        return [good] if state == "valid" else [good, bad] if state == "mixed" else [bad]

    monkeypatch.setattr(adapter, "get_ohlcv", get_ohlcv)
    pipeline = OperationalPipeline(
        settings=MapsSettings(maps_data_provider="mock"), session_factory=lambda: db,
    )
    monkeypatch.setattr(pipeline, "_make_krx_adapter", lambda: adapter)

    run = pipeline.backfill_ohlcv(bar.date, bar.date + dt.timedelta(days=len(days) - 1))

    assert run.status == expected_status
    assert run.details["rows"] == expected_rows
    assert run.details["partial_days"] == partial_days
    assert run.details["failed_days"] == failed_days
    assert db.query(HistoricalOHLCV).count() == expected_rows


def test_legacy_writer_cannot_overwrite_normalized_price(db, bar):
    collector=DataCollector(MockKRXAdapter(),db)
    collector._upsert_ohlcv([bar])
    row=db.query(HistoricalOHLCV).one();row.source='krx_per_security';db.commit()
    assert collector._upsert_ohlcv([replace(bar,close=4180,low=4180)])==0
    assert db.query(HistoricalOHLCV).one().close==4185


def test_legacy_adapter_rejects_current_session_before_network():
    from maps.data.krx_adapter import KRXAdapter
    from zoneinfo import ZoneInfo
    today=dt.datetime.now(ZoneInfo('Asia/Seoul')).date()
    with pytest.raises(DataCollectionError,match='legacy_history_requires_past_date'):
        KRXAdapter().get_legacy_history_ohlcv(today)
