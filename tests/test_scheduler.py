"""Operational scheduler tests."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from maps.common.db import Base
import datetime as dt
import math

from maps.common.models import (
    CandidateSnapshot,
    CollectionLog,
    HistoricalOHLCV,
    MonteCarloSequenceResults,
    OrderLog,
    ParameterPlateauResults,
    PromotionHistory,
    UniverseQualityLog,
    WalkForwardResults,
)
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


def test_order_cycle_submits_promoted_candidate_when_live_enabled() -> None:
    engine, factory = _session_factory()
    settings = MapsSettings(
        maps_broker_mode="mock",
        maps_data_provider="mock",
        maps_live_trading_enabled=True,
        max_single_exposure=0.10,
    )
    pipeline = OperationalPipeline(settings=settings, session_factory=factory)
    ref_date = dt.date(2026, 5, 5)

    db = factory()
    try:
        db.add(HistoricalOHLCV(
            ticker="AAAA",
            date=ref_date,
            open=10_000,
            high=10_000,
            low=10_000,
            close=10_000,
            volume=100_000,
        ))
        db.add(CandidateSnapshot(
            ref_date=ref_date,
            strategy_id="pullback_v3",
            ticker="AAAA",
            name="AAAA",
            market="KOSPI",
            factor_score=90,
            trend_strength=80,
            ts_bucket="S5",
            final_score=95,
            weekly_pass=True,
        ))
        db.add(PromotionHistory(
            strategy_id="pullback_v3",
            from_stage="alert_only",
            to_stage="mock_candidate",
            tradeability_score=70,
            passed=True,
            evaluated_at=dt.datetime(2026, 5, 5, 8, 0),
        ))
        db.commit()
    finally:
        db.close()

    run = pipeline.run_order_cycle(ref_date)

    assert run.status == "success"
    assert run.details["live_trading_enabled"] is True
    assert run.details["submitted_orders"] == 1

    db = factory()
    try:
        order = db.query(OrderLog).one()
        assert order.strategy_id == "pullback_v3"
        assert order.ticker == "AAAA"
        assert order.status == "filled"
        assert order.fill_qty == 1000
        row = db.query(CollectionLog).filter(CollectionLog.source == "scheduler.orders").first()
        assert row is not None
        assert row.status == "success"
        assert row.items == 1
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_validation_creates_promotion_history_from_latest_metrics() -> None:
    engine, factory = _session_factory()
    settings = MapsSettings(
        maps_broker_mode="mock",
        maps_data_provider="mock",
        maps_live_trading_enabled=False,
    )
    pipeline = OperationalPipeline(settings=settings, session_factory=factory)
    ref_date = dt.date(2026, 5, 5)

    db = factory()
    try:
        db.add(CandidateSnapshot(
            ref_date=ref_date,
            strategy_id="pullback_v3",
            ticker="AAAA",
            name="AAAA",
            market="KOSPI",
            factor_score=90,
            trend_strength=80,
            ts_bucket="S5",
            final_score=95,
            weekly_pass=True,
        ))
        db.add(ParameterPlateauResults(
            strategy_id="pullback_v3",
            run_date=ref_date,
            total_combinations=10,
            positive_combinations=8,
            positive_ratio=0.8,
            grade="A",
        ))
        db.add(MonteCarloSequenceResults(
            strategy_id="pullback_v3",
            strategy_group="pullback_short",
            run_date=ref_date,
            n_simulations=100,
            mdd_p95=0.09,
            mdd_limit=0.18,
            mc_within_limit=True,
        ))
        db.add(WalkForwardResults(
            strategy_id="pullback_v3",
            run_date=ref_date,
            n_folds=3,
            sharpe_mean=1.2,
            sharpe_std=0.25,
            negative_folds=0,
            mean_g2p=1.0,
            passed=True,
        ))
        db.commit()
    finally:
        db.close()

    run = pipeline.run_validation(ref_date)

    assert run.status == "success"
    assert run.details["status"] == "success"
    assert run.details["evaluated"] == 1
    assert run.details["passed"] == 1

    db = factory()
    try:
        promotion = db.query(PromotionHistory).one()
        assert promotion.strategy_id == "pullback_v3"
        assert promotion.from_stage == "research"
        assert promotion.to_stage == "mock_candidate"
        assert promotion.passed is True
        row = db.query(CollectionLog).filter(CollectionLog.source == "scheduler.validation").one()
        assert row.status == "success"
        assert row.items == 1
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_validation_generates_missing_metric_rows(monkeypatch) -> None:
    engine, factory = _session_factory()
    settings = MapsSettings(
        maps_broker_mode="mock",
        maps_data_provider="mock",
        maps_live_trading_enabled=False,
    )
    pipeline = OperationalPipeline(settings=settings, session_factory=factory)
    monkeypatch.setattr(OperationalPipeline, "_wfa_required_bars", staticmethod(lambda: 80))
    ref_date = dt.date(2026, 5, 5)

    db = factory()
    try:
        db.add(CandidateSnapshot(
            ref_date=ref_date,
            strategy_id="pullback_v3",
            ticker="AAAA",
            name="AAAA",
            market="KOSPI",
            factor_score=90,
            trend_strength=80,
            ts_bucket="S5",
            final_score=95,
            weekly_pass=True,
        ))
        start = dt.date(2025, 12, 15)
        for idx in range(100):
            day = start + dt.timedelta(days=idx)
            price = 10_000 + (idx * 10) + (math.sin(idx / 3) * 100)
            db.add(HistoricalOHLCV(
                ticker="AAAA",
                date=day,
                open=price * 0.99,
                high=price * 1.02,
                low=price * 0.98,
                close=price,
                volume=100_000,
            ))
        db.commit()
    finally:
        db.close()

    run = pipeline.run_validation(ref_date)

    assert run.status == "success"
    assert run.details["generated"]["plateau"] == 1
    assert run.details["generated"]["mc"] == 1
    assert run.details["generated"]["wfa"] == 1

    db = factory()
    try:
        assert db.query(ParameterPlateauResults).count() == 1
        assert db.query(MonteCarloSequenceResults).count() == 1
        assert db.query(WalkForwardResults).count() == 1
        assert db.query(PromotionHistory).count() == 1
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
