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


def test_current_weekday_market_day_does_not_require_live_ohlcv(monkeypatch) -> None:
    import maps.ops.scheduler as scheduler_module

    scheduler_module._krx_market_day_cache.clear()
    monkeypatch.setattr(scheduler_module, "is_krx_closed_date", lambda *args, **kwargs: False)

    assert scheduler_module._is_krx_market_day(dt.date.today()) is True


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

    rerun = pipeline.run_order_cycle(ref_date)
    assert rerun.status == "success"
    assert rerun.details["submitted_orders"] == 0

    db = factory()
    try:
        order = db.query(OrderLog).one()
        assert order.strategy_id == "pullback_v3"
        assert order.ticker == "AAAA"
        assert order.status == "filled"
        assert order.fill_qty == 990  # limit_price = close(10_000)*1.01 = 10_100 → 10_000_000//10_100
        row = db.query(CollectionLog).filter(CollectionLog.source == "scheduler.orders").first()
        assert row is not None
        assert row.status == "success"
        assert row.items == 1
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_order_cycle_skips_candidate_when_gap_exceeds_limit() -> None:
    """신호 이후 MAX_GAP 초과 상승 시 주문이 스킵되는지 검증."""
    engine, factory = _session_factory()
    settings = MapsSettings(
        maps_broker_mode="mock",
        maps_data_provider="mock",
        maps_live_trading_enabled=True,
        max_single_exposure=0.10,
        maps_order_max_gap_pct=0.02,   # 2% 갭 상한
        maps_order_slippage_pct=0.01,
    )
    pipeline = OperationalPipeline(settings=settings, session_factory=factory)
    signal_date = dt.date(2026, 5, 5)
    order_date  = dt.date(2026, 5, 8)   # 3일 후 — 3% 갭업

    db = factory()
    try:
        # 신호 발생일 종가 10,000
        db.add(HistoricalOHLCV(
            ticker="AAAA", date=signal_date,
            open=10_000, high=10_000, low=10_000, close=10_000, volume=100_000,
        ))
        # 주문일 직전 최신 종가 10,300 (갭 +3% → MAX_GAP 2% 초과)
        db.add(HistoricalOHLCV(
            ticker="AAAA", date=order_date - dt.timedelta(days=1),
            open=10_300, high=10_300, low=10_300, close=10_300, volume=100_000,
        ))
        db.add(CandidateSnapshot(
            ref_date=signal_date, strategy_id="pullback_v3", ticker="AAAA",
            name="AAAA", market="KOSPI", factor_score=90, trend_strength=80,
            ts_bucket="S5", final_score=95, weekly_pass=True,
        ))
        db.add(PromotionHistory(
            strategy_id="pullback_v3", from_stage="alert_only",
            to_stage="mock_candidate", tradeability_score=70, passed=True,
            evaluated_at=dt.datetime(2026, 5, 5, 8, 0),
        ))
        db.commit()
    finally:
        db.close()

    run = pipeline.run_order_cycle(order_date)

    assert run.status == "success"
    assert run.details["submitted_orders"] == 0   # 갭 초과로 스킵
    assert run.details["skipped_orders"] >= 1

    db = factory()
    try:
        assert db.query(OrderLog).count() == 0     # 주문 기록 없음
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_order_cycle_applies_slippage_to_limit_price() -> None:
    """slippage 1% 적용 시 지정가가 최신종가×1.01로 설정되는지 검증."""
    engine, factory = _session_factory()
    settings = MapsSettings(
        maps_broker_mode="mock",
        maps_data_provider="mock",
        maps_live_trading_enabled=True,
        max_single_exposure=0.10,
        maps_order_max_gap_pct=0.05,   # 5%로 넉넉히 — 갭 스킵 방지
        maps_order_slippage_pct=0.01,
    )
    pipeline = OperationalPipeline(settings=settings, session_factory=factory)
    ref_date = dt.date(2026, 5, 5)

    db = factory()
    try:
        db.add(HistoricalOHLCV(
            ticker="AAAA", date=ref_date,
            open=10_000, high=10_000, low=10_000, close=10_000, volume=100_000,
        ))
        db.add(CandidateSnapshot(
            ref_date=ref_date, strategy_id="pullback_v3", ticker="AAAA",
            name="AAAA", market="KOSPI", factor_score=90, trend_strength=80,
            ts_bucket="S5", final_score=95, weekly_pass=True,
        ))
        db.add(PromotionHistory(
            strategy_id="pullback_v3", from_stage="alert_only",
            to_stage="mock_candidate", tradeability_score=70, passed=True,
            evaluated_at=dt.datetime(2026, 5, 5, 8, 0),
        ))
        db.commit()
    finally:
        db.close()

    run = pipeline.run_order_cycle(ref_date)

    assert run.status == "success"
    assert run.details["submitted_orders"] == 1

    db = factory()
    try:
        order = db.query(OrderLog).one()
        # limit_price = int(10_000 * 1.01) = 10_100
        assert order.order_price == 10_100
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


def test_scheduler_registers_daily_stock_report_job() -> None:
    engine, factory = _session_factory()
    settings = MapsSettings(
        maps_broker_mode="mock",
        maps_data_provider="mock",
        maps_scheduler_enabled=False,
        maps_stock_report_time="15:00",
    )
    pipeline = OperationalPipeline(settings=settings, session_factory=factory)
    scheduler = MapsOperationalScheduler(settings=settings, pipeline=pipeline)

    scheduler._register_jobs()

    job = scheduler._scheduler.get_job("stock_report")
    assert job is not None
    assert str(job.trigger) == "cron[day_of_week='mon-fri', hour='15', minute='0']"

    Base.metadata.drop_all(engine)
    engine.dispose()


def test_stock_report_job_skips_krx_closed_day(monkeypatch) -> None:
    import maps.ops.scheduler as scheduler_module

    called = False

    def _run() -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(scheduler_module, "_is_krx_market_day", lambda: False)

    scheduler_module.MapsOperationalScheduler._make_krx_task("stock_report", _run)()

    assert called is False


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


def test_latest_promotion_rows_ignores_failed_evaluations() -> None:
    """_latest_promotion_rows 는 passed=True 레코드만 반환해야 한다.

    회귀 테스트:
      pullback_v3 가 mock_candidate 로 승격(passed=True)된 뒤
      다음 평가에서 live_candidate 승격 실패(passed=False)가 기록되더라도
      현재 단계는 여전히 mock_candidate 여야 한다.
      이전 버그: _latest_promotion_rows 가 passed=False 레코드도 읽어
      전략을 RESEARCH 단계로 강제 강등시켰다.
    """
    engine, factory = _session_factory()

    db = factory()
    try:
        # 1차 평가: research → mock_candidate 승격 (passed=True)
        db.add(PromotionHistory(
            strategy_id="pullback_v3",
            from_stage="research",
            to_stage="mock_candidate",
            tradeability_score=71.8,
            passed=True,
            evaluated_at=dt.datetime(2026, 5, 20, 16, 40),
        ))
        # 2차 평가: live_candidate 시도 실패 (passed=False)
        # 점수 71.8 < 임계값 75 — 승격 불가이지만 강등도 안 돼야 함
        db.add(PromotionHistory(
            strategy_id="pullback_v3",
            from_stage="mock_candidate",
            to_stage="rejected",  # gate 가 to_stage=REJECTED 로 기록하는 현재 구조
            tradeability_score=71.8,
            passed=False,
            evaluated_at=dt.datetime(2026, 5, 21, 16, 40),
        ))
        db.commit()
    finally:
        db.close()

    db = factory()
    try:
        latest = OperationalPipeline._latest_promotion_rows(db)
        # passed=True 레코드만 봐야 하므로 mock_candidate 여야 한다
        assert "pullback_v3" in latest
        assert latest["pullback_v3"].to_stage == "mock_candidate"
        assert latest["pullback_v3"].passed is True
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_latest_promotions_ignores_failed_evaluations() -> None:
    """_latest_promotions (주문 주기용) 도 passed=True 만 봐야 한다.

    회귀 테스트:
      mock_candidate 승격 후 실패 평가가 기록되더라도
      주문 자격(eligible_stages)은 유지돼야 한다.
    """
    engine, factory = _session_factory()

    db = factory()
    try:
        db.add(PromotionHistory(
            strategy_id="pullback_v3",
            from_stage="research",
            to_stage="mock_candidate",
            tradeability_score=71.8,
            passed=True,
            evaluated_at=dt.datetime(2026, 5, 20, 16, 40),
        ))
        db.add(PromotionHistory(
            strategy_id="pullback_v3",
            from_stage="mock_candidate",
            to_stage="rejected",
            tradeability_score=71.8,
            passed=False,
            evaluated_at=dt.datetime(2026, 5, 21, 16, 40),
        ))
        db.commit()
    finally:
        db.close()

    db = factory()
    try:
        latest = OperationalPipeline._latest_promotions(db)
        assert latest.get("pullback_v3") == "mock_candidate"
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()
