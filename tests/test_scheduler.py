"""Operational scheduler tests."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from maps.common.db import Base
from maps.common.models import CollectionLog, HistoricalOHLCV, UniverseQualityLog
from maps.common.settings import MapsSettings
from maps.ops.scheduler import MapsOperationalScheduler, OperationalPipeline

import maps.common.models  # noqa: F401


def _session_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine, sessionmaker(autocommit=False, autoflush=False, bind=engine)


def test_pipeline_collect_and_candidate_generation_with_mock_provider() -> None:
    engine, factory = _session_factory()
    settings = MapsSettings(
        maps_broker_mode="mock",
        maps_data_provider="mock",
        maps_live_trading_enabled=False,
    )
    pipeline = OperationalPipeline(settings=settings, session_factory=factory)

    collect = pipeline.collect_data()
    candidates = pipeline.generate_candidates()

    assert collect.status == "success"
    assert collect.details["ohlcv_count"] == 3
    assert candidates.status == "success"
    assert candidates.details["kept_count"] == 3

    db = factory()
    try:
        assert db.query(CollectionLog).filter(CollectionLog.source == "krx").count() >= 1
        assert db.query(HistoricalOHLCV).count() == 3
        assert db.query(UniverseQualityLog).count() == 1
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_order_cycle_is_sync_only_when_live_disabled() -> None:
    engine, factory = _session_factory()
    settings = MapsSettings(
        maps_broker_mode="mock",
        maps_data_provider="mock",
        maps_live_trading_enabled=False,
    )
    pipeline = OperationalPipeline(settings=settings, session_factory=factory)

    run = pipeline.run_order_cycle()

    assert run.status == "success"
    assert run.details["live_trading_enabled"] is False
    assert run.details["submitted_orders"] == 0

    db = factory()
    try:
        row = db.query(CollectionLog).filter(CollectionLog.source == "scheduler.orders").first()
        assert row is not None
        assert row.status == "skipped"
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_scheduler_status_and_run_once() -> None:
    engine, factory = _session_factory()
    settings = MapsSettings(
        maps_broker_mode="mock",
        maps_data_provider="mock",
        maps_scheduler_enabled=False,
    )
    pipeline = OperationalPipeline(settings=settings, session_factory=factory)
    scheduler = MapsOperationalScheduler(settings=settings, pipeline=pipeline)

    run = scheduler.run_once("validation")
    status = scheduler.status()

    assert run.status == "success"
    assert status["enabled"] is False
    assert status["running"] is False
    assert status["last_runs"]["validation"]["details"]["status"] == "skipped"

    Base.metadata.drop_all(engine)
    engine.dispose()


def test_scheduler_backfill_ohlcv() -> None:
    import datetime as dt

    engine, factory = _session_factory()
    settings = MapsSettings(
        maps_broker_mode="mock",
        maps_data_provider="mock",
        maps_scheduler_enabled=False,
    )
    pipeline = OperationalPipeline(settings=settings, session_factory=factory)
    scheduler = MapsOperationalScheduler(settings=settings, pipeline=pipeline)

    run = scheduler.backfill_ohlcv(dt.date(2026, 5, 1), dt.date(2026, 5, 1))

    assert run.status == "success"
    assert run.details["rows"] == 3

    db = factory()
    try:
        assert db.query(HistoricalOHLCV).count() == 3
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()
