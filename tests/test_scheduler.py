"""Operational scheduler tests."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from maps.common.db import Base
import datetime as dt
import math
import pytest
from types import SimpleNamespace

from maps.common.models import (
    BacktestRunLog,
    CandidateSnapshot,
    CollectionLog,
    HistoricalOHLCV,
    JobRunLog,
    MonteCarloSequenceResults,
    OrderLog,
    ParameterPlateauResults,
    PortfolioSnapshot,
    PromotionHistory,
    SecurityMetadata,
    UniverseQualityLog,
    WalkForwardResults,
)
from maps.common.exceptions import BrokerAdapterError
from maps.common.settings import MapsSettings
from maps.execution.broker_adapter import Order, OrderSide, OrderType
from maps.execution.mock_broker import MockBroker
from maps.execution.order_manager import OrderManager
from maps.market.regime import RegimeLabel, RegimeResult, WeeklyTrendLabel
from maps.ops.scheduler import MapsOperationalScheduler, OperationalPipeline, StrategySignal
from maps.risk.manager import RiskManager

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


def test_scheduled_backtest_summary_uses_best_sharpe_and_upserts() -> None:
    """자동 검증 대표 이력은 최고 Sharpe 조합을 날짜·전략별 한 건으로 남긴다."""
    engine, factory = _session_factory()
    db = factory()
    strategy = SimpleNamespace(strategy_id="pullback_v3")
    ref_date = dt.date(2026, 8, 5)
    rows = [
        {
            "rsi_period": 3,
            "sharpe": 0.4,
            "mdd": -0.12,
            "daily_returns": [0.01] * 30,
            "_net_cagr": 0.05,
            "_trade_count": 20,
            "_ticker_count": 2,
            "_tickers": ["005930", "000660"],
            "_start_date": dt.date(2020, 1, 2),
            "_end_date": ref_date,
        },
        {
            "rsi_period": 5,
            "sharpe": 0.9,
            "mdd": -0.18,
            "daily_returns": [0.02] * 30,
            "_net_cagr": 0.11,
            "_trade_count": 35,
            "_ticker_count": 2,
            "_tickers": ["005930", "000660"],
            "_start_date": dt.date(2020, 1, 2),
            "_end_date": ref_date,
        },
    ]
    try:
        OperationalPipeline._save_scheduled_backtest(db, strategy, ref_date, rows)
        first = db.query(BacktestRunLog).one()
        assert first.source == "scheduled_validation"
        assert first.sharpe == pytest.approx(0.9)
        assert first.net_cagr == pytest.approx(0.11)
        assert first.trade_count == 35
        assert first.mode == "per_ticker"
        assert first.universe == "validation_sample"
        assert '"rsi_period": 5' in first.params_json

        rows[1]["_net_cagr"] = 0.13
        OperationalPipeline._save_scheduled_backtest(db, strategy, ref_date, rows)
        assert db.query(BacktestRunLog).count() == 1
        assert db.query(BacktestRunLog).one().net_cagr == pytest.approx(0.13)
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _force_entry_signal(monkeypatch) -> None:
    # 실제 dataclass 를 쓴다. SimpleNamespace 로 흉내내면 프로덕션이 새 필드를
    # 읽기 시작했을 때 테스트만 AttributeError 로 깨진다(atr14 에서 실제로 겪었다).
    monkeypatch.setattr(
        OperationalPipeline,
        "_latest_strategy_signal",
        staticmethod(
            lambda *args, **kwargs: StrategySignal(
                entry_signal=True, exit_signal=False, close=0.0
            )
        ),
    )


def _force_regime_pass(monkeypatch) -> None:
    monkeypatch.setattr(
        OperationalPipeline,
        "_analyze_regime",
        lambda self: RegimeResult(
            regime=RegimeLabel.MIXED,
            weekly_trend=WeeklyTrendLabel.PASS,
            limit_ratio=0.5,
            kospi_ts=None,
        ),
    )


def _add_fresh_ohlcv(db, ref_date: dt.date, ticker: str = "AAAA", close: float = 10_000.0) -> None:
    """테스트용 OHLCV를 MIN_FRESH_TICKERS 이상 삽입해 _is_data_fresh=True 조건을 충족시킨다."""
    db.add(HistoricalOHLCV(
        ticker=ticker, date=ref_date,
        open=int(close), high=int(close), low=int(close), close=int(close), volume=100_000,
    ))
    # 나머지 티커는 더미 값으로 채운다 (MIN_FRESH_TICKERS - 1개)
    for i in range(OperationalPipeline._MIN_FRESH_TICKERS - 1):
        dummy_ticker = f"DUMMY{i:04d}"
        if dummy_ticker == ticker:
            continue
        db.add(HistoricalOHLCV(
            ticker=dummy_ticker, date=ref_date,
            open=1_000, high=1_000, low=1_000, close=1_000, volume=1_000,
        ))


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


def test_order_cycle_submits_promoted_candidate_when_live_enabled(monkeypatch) -> None:
    engine, factory = _session_factory()
    settings = MapsSettings(
        maps_broker_mode="mock",
        maps_data_provider="mock",
        maps_live_trading_enabled=True,
        max_single_exposure=0.10,
    )
    pipeline = OperationalPipeline(settings=settings, session_factory=factory)
    _force_entry_signal(monkeypatch)
    _force_regime_pass(monkeypatch)
    ref_date = dt.date(2026, 5, 5)

    db = factory()
    try:
        _add_fresh_ohlcv(db, ref_date, ticker="AAAA", close=10_000.0)
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
            from_stage="mock_candidate",
            to_stage="live_candidate",
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
        # limit_price = close(10_000) × 1.01 = 10,100.
        # 손절 10,100 × 0.95 = 9,595 → 호가 10원 단위 내림 9,590 → 손절폭 510.
        # 계좌위험 0.5%(500,000) ÷ 510 = 980 주. 고정비중 상한(990)보다 작아 이쪽이 결정한다.
        assert order.fill_qty == 980
        row = db.query(CollectionLog).filter(CollectionLog.source == "scheduler.orders").first()
        assert row is not None
        assert row.status == "success"
        assert row.items == 1
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_order_cycle_skips_candidate_when_gap_exceeds_limit(monkeypatch) -> None:
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
    _force_entry_signal(monkeypatch)
    _force_regime_pass(monkeypatch)
    # 후보는 주문일 직전 거래일(5/7)에 생성 → 신선도 가드 통과.
    # 주문 당일(5/8) 종가가 +3% 갭업하여 MAX_GAP(2%)을 초과하는 시나리오.
    signal_date = dt.date(2026, 5, 7)   # previous_trading_day(5/8) — 신선
    order_date  = dt.date(2026, 5, 8)

    db = factory()
    try:
        # 신호일(직전 거래일) 종가 10,000 + 신선도 충족용 더미 티커
        _add_fresh_ohlcv(db, signal_date, ticker="AAAA", close=10_000.0)
        # 주문 당일 최신 종가 10,300 (갭 +3% → MAX_GAP 2% 초과)
        db.add(HistoricalOHLCV(
            ticker="AAAA", date=order_date,
            open=10_300, high=10_300, low=10_300, close=10_300, volume=100_000,
        ))
        db.add(CandidateSnapshot(
            ref_date=signal_date, strategy_id="pullback_v3", ticker="AAAA",
            name="AAAA", market="KOSPI", factor_score=90, trend_strength=80,
            ts_bucket="S5", final_score=95, weekly_pass=True,
        ))
        db.add(PromotionHistory(
            strategy_id="pullback_v3", from_stage="mock_candidate",
            to_stage="live_candidate", tradeability_score=70, passed=True,
            evaluated_at=dt.datetime(2026, 5, 7, 8, 0),
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


def test_order_cycle_applies_slippage_to_limit_price(monkeypatch) -> None:
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
    _force_entry_signal(monkeypatch)
    _force_regime_pass(monkeypatch)
    ref_date = dt.date(2026, 5, 5)

    db = factory()
    try:
        _add_fresh_ohlcv(db, ref_date, ticker="AAAA", close=10_000.0)
        db.add(CandidateSnapshot(
            ref_date=ref_date, strategy_id="pullback_v3", ticker="AAAA",
            name="AAAA", market="KOSPI", factor_score=90, trend_strength=80,
            ts_bucket="S5", final_score=95, weekly_pass=True,
        ))
        db.add(PromotionHistory(
            strategy_id="pullback_v3", from_stage="mock_candidate",
            to_stage="live_candidate", tradeability_score=70, passed=True,
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


def test_order_cycle_skips_candidate_without_strategy_entry_signal(monkeypatch) -> None:
    engine, factory = _session_factory()
    settings = MapsSettings(
        maps_broker_mode="mock",
        maps_data_provider="mock",
        maps_live_trading_enabled=True,
    )
    pipeline = OperationalPipeline(settings=settings, session_factory=factory)
    _force_regime_pass(monkeypatch)
    ref_date = dt.date(2026, 5, 5)

    db = factory()
    try:
        _add_fresh_ohlcv(db, ref_date, ticker="AAAA", close=10_000.0)
        db.add(CandidateSnapshot(
            ref_date=ref_date, strategy_id="pullback_v3", ticker="AAAA",
            name="AAAA", market="KOSPI", factor_score=90, trend_strength=80,
            ts_bucket="S5", final_score=95, weekly_pass=True,
        ))
        db.add(PromotionHistory(
            strategy_id="pullback_v3", from_stage="mock_candidate",
            to_stage="live_candidate", tradeability_score=70, passed=True,
            evaluated_at=dt.datetime(2026, 5, 5, 8, 0),
        ))
        db.commit()
    finally:
        db.close()

    run = pipeline.run_order_cycle(ref_date)

    assert run.status == "success"
    assert run.details["submitted_buy_orders"] == 0
    assert run.details["skipped_buy_orders"] == 1

    db = factory()
    try:
        assert db.query(OrderLog).count() == 0
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_submit_exit_orders_sells_position_when_stop_loss_is_reached() -> None:
    engine, factory = _session_factory()
    settings = MapsSettings(
        maps_broker_mode="mock",
        maps_data_provider="mock",
        maps_live_trading_enabled=True,
    )
    pipeline = OperationalPipeline(settings=settings, session_factory=factory)
    broker = MockBroker(initial_cash=1_000_000, price_feed={"AAAA": 10_000})
    broker.place_order(Order(
        strategy_id="pullback_v3",
        ticker="AAAA",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=10,
    ))
    broker.set_price("AAAA", 9_400)
    ref_date = dt.date(2026, 5, 5)

    db = factory()
    try:
        db.add(OrderLog(
            order_id="entry-AAAA",
            strategy_id="pullback_v3",
            ticker="AAAA",
            side="buy",
            qty=10,
            order_price=10_000,
            fill_price=10_000,
            fill_qty=10,
            status="filled",
        ))
        db.add(HistoricalOHLCV(
            ticker="AAAA", date=ref_date,
            open=9_400, high=9_400, low=9_400, close=9_400, volume=100_000,
        ))
        db.commit()
        manager = OrderManager(broker=broker, risk=RiskManager(broker=broker, db=db), db=db)

        submitted, skipped, exit_tickers = pipeline._submit_exit_orders(
            db=db,
            broker=broker,
            manager=manager,
            ref_date=ref_date,
        )

        assert submitted == 1
        assert skipped == 0
        assert exit_tickers == {"AAAA"}
        sell = db.query(OrderLog).filter(OrderLog.side == "sell").one()
        assert sell.status == "filled"
        assert sell.fill_qty == 10
        assert broker.get_position("AAAA") is None
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_broker_sync_submits_stop_loss_exit_during_market_hours(monkeypatch) -> None:
    import maps.ops.scheduler as scheduler_module

    engine, factory = _session_factory()
    settings = MapsSettings(
        maps_broker_mode="mock",
        maps_data_provider="mock",
        maps_live_trading_enabled=True,
    )
    pipeline = OperationalPipeline(settings=settings, session_factory=factory)
    broker = MockBroker(initial_cash=1_000_000, price_feed={"AAAA": 10_000})
    broker.place_order(Order(
        strategy_id="pullback_v3",
        ticker="AAAA",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=10,
    ))
    broker._filled.clear()
    broker.set_price("AAAA", 9_400)
    monkeypatch.setattr(broker, "is_market_open", lambda: True)
    monkeypatch.setattr(scheduler_module, "get_broker", lambda _mode: broker)
    ref_date = dt.date(2026, 5, 5)

    db = factory()
    try:
        db.add(OrderLog(
            order_id="entry-AAAA",
            strategy_id="pullback_v3",
            ticker="AAAA",
            side="buy",
            qty=10,
            order_price=10_000,
            fill_price=10_000,
            fill_qty=10,
            status="filled",
        ))
        db.add(HistoricalOHLCV(
            ticker="AAAA", date=ref_date,
            open=9_400, high=9_400, low=9_400, close=9_400, volume=100_000,
        ))
        db.commit()
    finally:
        db.close()

    run = pipeline.sync_broker_state(ref_date)

    assert run.status == "success"
    assert run.details["exit_monitor_active"] is True
    assert run.details["submitted_sell_orders"] == 1
    assert run.details["exit_tickers"] == ["AAAA"]

    db = factory()
    try:
        sell = db.query(OrderLog).filter(OrderLog.side == "sell").one()
        assert sell.status == "filled"
        assert sell.fill_qty == 10
        row = db.query(CollectionLog).filter(CollectionLog.source == "scheduler.broker_sync").one()
        assert row.items == 1
        assert "exit_monitor=on" in (row.note or "")
        assert broker.get_position("AAAA") is None
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_broker_sync_note_records_holdings_count(monkeypatch) -> None:
    """broker_sync 감사 로그 note에 보유 종목 수가 남는다.

    KIS 연속조회(tr_cont)가 잔고를 20종목에서 자르지 않는지 사후 확인하는 지표다.
    portfolio_snapshot은 (ref_date, source) 유니크 upsert라 날짜당 마지막 값만 남지만,
    이 note는 60초마다 한 행씩 쌓여 장중 변동까지 보존한다.
    """
    import maps.ops.scheduler as scheduler_module

    engine, factory = _session_factory()
    settings = MapsSettings(
        maps_broker_mode="mock",
        maps_data_provider="mock",
        maps_live_trading_enabled=False,
    )
    pipeline = OperationalPipeline(settings=settings, session_factory=factory)
    broker = MockBroker(initial_cash=1_000_000, price_feed={"AAAA": 10_000, "BBBB": 5_000})
    for ticker in ("AAAA", "BBBB"):
        broker.place_order(Order(
            strategy_id="pullback_v3",
            ticker=ticker,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=10,
        ))
    monkeypatch.setattr(scheduler_module, "get_broker", lambda _mode: broker)

    run = pipeline.sync_broker_state(dt.date(2026, 5, 5))

    assert run.status == "success"
    db = factory()
    try:
        row = db.query(CollectionLog).filter(CollectionLog.source == "scheduler.broker_sync").one()
        assert "holdings=2" in (row.note or "")
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_broker_sync_note_marks_holdings_unavailable_when_broker_cannot_report(monkeypatch) -> None:
    """포지션 조회를 지원하지 않는 브로커면 `holdings=n/a` — 0과 구분해야 한다.

    `_broker_positions`는 미지원 시 None, 전량 미보유 시 빈 dict를 돌려준다. 둘을 같은
    `holdings=0`으로 적으면 "잔고 조회가 죽었다"와 "정말 하나도 없다"를 구분할 수 없다.
    """
    import maps.ops.scheduler as scheduler_module

    engine, factory = _session_factory()
    settings = MapsSettings(
        maps_broker_mode="mock",
        maps_data_provider="mock",
        maps_live_trading_enabled=False,
    )
    pipeline = OperationalPipeline(settings=settings, session_factory=factory)
    broker = MockBroker(initial_cash=1_000_000)

    def _unsupported() -> dict[str, int]:
        raise NotImplementedError

    monkeypatch.setattr(broker, "get_positions", _unsupported)
    monkeypatch.setattr(scheduler_module, "get_broker", lambda _mode: broker)

    run = pipeline.sync_broker_state(dt.date(2026, 5, 5))

    assert run.status == "success"
    db = factory()
    try:
        row = db.query(CollectionLog).filter(CollectionLog.source == "scheduler.broker_sync").one()
        assert "holdings=n/a" in (row.note or "")
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_broker_sync_does_not_submit_exit_when_market_is_closed(monkeypatch) -> None:
    import maps.ops.scheduler as scheduler_module

    engine, factory = _session_factory()
    settings = MapsSettings(
        maps_broker_mode="mock",
        maps_data_provider="mock",
        maps_live_trading_enabled=True,
    )
    pipeline = OperationalPipeline(settings=settings, session_factory=factory)
    broker = MockBroker(initial_cash=1_000_000, price_feed={"AAAA": 10_000})
    broker.place_order(Order(
        strategy_id="pullback_v3",
        ticker="AAAA",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=10,
    ))
    broker._filled.clear()
    broker.set_price("AAAA", 9_400)
    monkeypatch.setattr(broker, "is_market_open", lambda: False)
    monkeypatch.setattr(scheduler_module, "get_broker", lambda _mode: broker)
    ref_date = dt.date(2026, 5, 5)

    db = factory()
    try:
        db.add(OrderLog(
            order_id="entry-AAAA",
            strategy_id="pullback_v3",
            ticker="AAAA",
            side="buy",
            qty=10,
            order_price=10_000,
            fill_price=10_000,
            fill_qty=10,
            status="filled",
        ))
        db.add(HistoricalOHLCV(
            ticker="AAAA", date=ref_date,
            open=9_400, high=9_400, low=9_400, close=9_400, volume=100_000,
        ))
        db.commit()
    finally:
        db.close()

    run = pipeline.sync_broker_state(ref_date)

    assert run.status == "success"
    assert run.details["exit_monitor_active"] is False
    assert run.details["submitted_sell_orders"] == 0

    db = factory()
    try:
        assert db.query(OrderLog).filter(OrderLog.side == "sell").count() == 0
        assert broker.get_position("AAAA") is not None
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


def test_save_portfolio_snapshot_uses_broker_total_assets() -> None:
    engine, factory = _session_factory()
    db = factory()
    try:
        OperationalPipeline._save_portfolio_snapshot(
            db,
            dt.date(2026, 6, 1),
            {
                "cash": 82_301_500,
                "positions_value": 17_566_000,
                "total_assets": 99_867_500,
            },
        )

        row = db.query(PortfolioSnapshot).one()
        assert row.cash == 82_301_500
        assert row.positions_value == 17_566_000
        assert row.total_assets == 99_867_500
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_save_portfolio_snapshot_clears_holdings_when_all_sold() -> None:
    """전량 매도 후 빈 holdings({})는 저장되고, 조회 불가(None)는 기존 값을 유지해야 한다."""
    engine, factory = _session_factory()
    db = factory()
    try:
        sync = {"cash": 100.0, "positions_value": 50.0, "total_assets": 150.0}
        OperationalPipeline._save_portfolio_snapshot(
            db, dt.date(2026, 7, 13), sync, holdings={"004490": 146},
        )
        OperationalPipeline._save_portfolio_snapshot(
            db, dt.date(2026, 7, 13), sync, holdings={},
        )
        row = db.query(PortfolioSnapshot).one()
        assert row.holdings == {}

        OperationalPipeline._save_portfolio_snapshot(
            db, dt.date(2026, 7, 13), sync, holdings=None,
        )
        db.expire_all()
        row = db.query(PortfolioSnapshot).one()
        assert row.holdings == {}
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_portfolio_snapshot_stores_detailed_position_values() -> None:
    engine, factory = _session_factory()
    db = factory()
    try:
        db.add(SecurityMetadata(
            ticker="AAAA", name="테스트 종목", market="KOSPI", security_type="stock",
        ))
        db.commit()
        broker = MockBroker(initial_cash=1_000_000, price_feed={"AAAA": 11_000})
        broker.place_order(Order(
            strategy_id="test", ticker="AAAA", side=OrderSide.BUY,
            order_type=OrderType.LIMIT, quantity=10, limit_price=10_000,
        ))

        holdings, details = OperationalPipeline._portfolio_snapshot_positions(db, broker)
        OperationalPipeline._save_portfolio_snapshot(
            db,
            dt.date(2026, 8, 20),
            {"cash": 900_000, "positions_value": 110_000, "total_assets": 1_010_000},
            holdings=holdings,
            holding_details=details,
        )

        row = db.query(PortfolioSnapshot).one()
        assert row.holdings == {"AAAA": 10}
        assert row.holding_details == {
            "AAAA": {
                "name": "테스트 종목",
                "quantity": 10,
                "avg_price": 10_000.0,
                "current_price": 11_000.0,
                "evaluation_value": 110_000.0,
                "unrealized_pnl": 10_000.0,
                "unrealized_pnl_pct": 0.1,
            }
        }
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_broker_positions_distinguishes_empty_from_unsupported() -> None:
    """빈 포지션({})은 유효한 상태로 통과시키고, 조회 미지원은 None을 반환해야 한다."""

    class _Unsupported:
        def get_positions(self) -> dict[str, int]:
            raise NotImplementedError

    class _Empty:
        def get_positions(self) -> dict[str, int]:
            return {}

    assert OperationalPipeline._broker_positions(_Unsupported()) is None
    assert OperationalPipeline._broker_positions(_Empty()) == {}


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
    assert str(job.trigger) == "cron[hour='15', minute='0']"

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


def test_scheduler_backfill_fundamentals() -> None:
    import datetime as dt

    engine, factory = _session_factory()
    settings = MapsSettings(
        maps_broker_mode="mock",
        maps_data_provider="mock",
        maps_scheduler_enabled=False,
    )
    pipeline = OperationalPipeline(settings=settings, session_factory=factory)
    scheduler = MapsOperationalScheduler(settings=settings, pipeline=pipeline)

    # 2026-05-01 (금) 단일 영업일 백필. MockKRXAdapter 는 펀더멘털 오버라이드가 없어
    # rows==0 이지만, JobRun 플러밍과 collection_log 배선이 동작해야 한다.
    run = scheduler.backfill_fundamentals(dt.date(2026, 5, 1), dt.date(2026, 5, 1))

    assert run.status == "success"
    assert run.details["rows"] == 0
    assert run.details["business_days"] == 1

    db = factory()
    try:
        logs = (
            db.query(CollectionLog)
            .filter(CollectionLog.source == "krx.fundamental")
            .all()
        )
        assert len(logs) == 1
        assert logs[0].status == "success"
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


def test_latest_promotions_keeps_stage_after_latest_failed_evaluation() -> None:
    """_latest_promotions (주문 주기용) 도 passed=True 만 봐야 한다.

    회귀 테스트:
      mock_candidate 승격 후 실패 평가(passed=False)가 기록되더라도
      주문 자격(eligible_stages)은 유지돼야 한다. 실패 레코드의 to_stage 가
      "rejected" 여도 무시하고 마지막 성공 단계(mock_candidate)를 반환한다.
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


# ── C-1: 일일 손익률 계산 테스트 ─────────────────────────────────────────────

def test_calc_daily_pnl_returns_correct_ratio() -> None:
    """전일 대비 오늘 total_assets 차이가 비율로 정확히 반환되는지 검증."""
    engine, factory = _session_factory()
    ref_date = dt.date(2026, 5, 8)
    prev_date = dt.date(2026, 5, 7)

    db = factory()
    try:
        db.add(PortfolioSnapshot(
            ref_date=prev_date, source="broker",
            total_assets=100_000_000, cash=100_000_000, positions_value=0,
        ))
        db.add(PortfolioSnapshot(
            ref_date=ref_date, source="broker",
            total_assets=95_000_000, cash=95_000_000, positions_value=0,
        ))
        db.commit()

        pnl = OperationalPipeline._calc_daily_pnl(db, ref_date)
        assert abs(pnl - (-0.05)) < 1e-9
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_calc_daily_pnl_returns_zero_when_no_previous_snapshot() -> None:
    """전일 스냅샷이 없으면 0.0을 반환해 Kill Switch가 발동되지 않는지 검증."""
    engine, factory = _session_factory()
    ref_date = dt.date(2026, 5, 8)

    db = factory()
    try:
        db.add(PortfolioSnapshot(
            ref_date=ref_date, source="broker",
            total_assets=100_000_000, cash=100_000_000, positions_value=0,
        ))
        db.commit()

        pnl = OperationalPipeline._calc_daily_pnl(db, ref_date)
        assert pnl == 0.0
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


# ── C-2/C-3: 시황 오버라이드 + 진입 한도 적용 테스트 ──────────────────────────

def test_order_cycle_skips_all_buys_when_regime_override_is_fail(monkeypatch) -> None:
    """MAPS_WEEKLY_TREND_OVERRIDE=fail 일 때 매수 주문이 전량 스킵되는지 검증."""
    engine, factory = _session_factory()
    settings = MapsSettings(
        maps_broker_mode="mock",
        maps_data_provider="mock",
        maps_live_trading_enabled=True,
        maps_weekly_trend_override="fail",
    )
    pipeline = OperationalPipeline(settings=settings, session_factory=factory)
    _force_entry_signal(monkeypatch)
    ref_date = dt.date(2026, 5, 5)

    db = factory()
    try:
        _add_fresh_ohlcv(db, ref_date, ticker="AAAA", close=10_000.0)
        db.add(CandidateSnapshot(
            ref_date=ref_date, strategy_id="pullback_v3", ticker="AAAA",
            name="AAAA", market="KOSPI", factor_score=90, trend_strength=80,
            ts_bucket="S5", final_score=95, weekly_pass=True,
        ))
        db.add(PromotionHistory(
            strategy_id="pullback_v3", from_stage="mock_candidate",
            to_stage="live_candidate", tradeability_score=70, passed=True,
            evaluated_at=dt.datetime(2026, 5, 5, 8, 0),
        ))
        db.commit()
    finally:
        db.close()

    run = pipeline.run_order_cycle(ref_date)

    assert run.status == "success"
    assert run.details["submitted_buy_orders"] == 0

    db = factory()
    try:
        assert db.query(OrderLog).filter(OrderLog.side == "buy").count() == 0
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_generate_candidates_sets_weekly_pass_false_when_trend_override_is_fail() -> None:
    """MAPS_WEEKLY_TREND_OVERRIDE=fail 일 때 후보 weekly_pass 가 False로 저장되는지 검증."""
    engine, factory = _session_factory()
    settings = MapsSettings(
        maps_broker_mode="mock",
        maps_data_provider="mock",
        maps_live_trading_enabled=False,
        maps_weekly_trend_override="fail",
    )
    pipeline = OperationalPipeline(settings=settings, session_factory=factory)

    pipeline.collect_data()
    pipeline.generate_candidates()

    db = factory()
    try:
        snapshots = db.query(CandidateSnapshot).all()
        assert len(snapshots) > 0
        assert all(not snap.weekly_pass for snap in snapshots), (
            "모든 후보의 weekly_pass 가 False 여야 함 (trend=fail)"
        )
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_kill_switch_triggers_when_daily_pnl_exceeds_limit(monkeypatch) -> None:
    """daily_pnl 이 -1.5% 이하일 때 Kill Switch 가 발동되어 매수가 차단되는지 검증.

    run_order_cycle() 이 생성하는 MockBroker 를 98M 잔고로 교체해 -2% 손실을 시뮬레이션.
    """
    import maps.ops.scheduler as scheduler_module

    engine, factory = _session_factory()
    settings = MapsSettings(
        maps_broker_mode="mock",
        maps_data_provider="mock",
        maps_live_trading_enabled=True,
        daily_loss_limit=0.015,
    )
    pipeline = OperationalPipeline(settings=settings, session_factory=factory)
    _force_entry_signal(monkeypatch)
    _force_regime_pass(monkeypatch)
    ref_date = dt.date(2026, 5, 8)
    prev_date = dt.date(2026, 5, 7)

    # 전일 스냅샷 100M, 오늘 브로커 잔고 98M (-2%) 시뮬레이션
    loss_broker = MockBroker(initial_cash=98_000_000)
    monkeypatch.setattr(scheduler_module, "get_broker", lambda _mode: loss_broker)

    db = factory()
    try:
        db.add(PortfolioSnapshot(
            ref_date=prev_date, source="broker",
            total_assets=100_000_000, cash=100_000_000, positions_value=0,
        ))
        _add_fresh_ohlcv(db, ref_date, ticker="AAAA", close=10_000.0)
        db.add(CandidateSnapshot(
            ref_date=ref_date, strategy_id="pullback_v3", ticker="AAAA",
            name="AAAA", market="KOSPI", factor_score=90, trend_strength=80,
            ts_bucket="S5", final_score=95, weekly_pass=True,
        ))
        db.add(PromotionHistory(
            strategy_id="pullback_v3", from_stage="mock_candidate",
            to_stage="live_candidate", tradeability_score=70, passed=True,
            evaluated_at=dt.datetime(2026, 5, 8, 8, 0),
        ))
        db.commit()
    finally:
        db.close()

    run = pipeline.run_order_cycle(ref_date)

    assert run.status == "success"
    assert run.details["submitted_buy_orders"] == 0, (
        "일일 손실 한도 초과로 Kill Switch 발동 → 매수 0건 기대"
    )

    db = factory()
    try:
        from maps.common.models import KillSwitchLog
        ks = db.query(KillSwitchLog).filter(KillSwitchLog.strategy_id == "pullback_v3").first()
        assert ks is not None, "Kill Switch 로그가 기록되어야 함"
        assert ks.reason == "daily_loss_limit"
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


# ---------------------------------------------------------------------------
# H-1: MockBroker 현재가 기반 포지션 평가 (스케줄러 통합)
# ---------------------------------------------------------------------------

def test_order_cycle_account_balance_reflects_updated_price(monkeypatch) -> None:
    """MockBroker price_feed 갱신 후 get_account_balance() 가 시가 평가를 반환한다."""
    broker = MockBroker(initial_cash=1_000_000, price_feed={"AAAA": 10_000})
    from maps.execution.broker_adapter import Order, OrderSide, OrderType
    broker.place_order(Order(
        strategy_id="s1", ticker="AAAA", side=OrderSide.BUY,
        order_type=OrderType.MARKET, quantity=10,
    ))
    # 가격 하락 반영
    broker.set_price("AAAA", 8_000)

    balance = broker.get_account_balance()
    assert balance.positions_value == 10 * 8_000
    assert balance.total_value == 900_000 + 80_000


# ---------------------------------------------------------------------------
# H-2: 장중 현재가 조회 메서드 단위 테스트
# ---------------------------------------------------------------------------

def test_fetch_intraday_prices_returns_empty_when_pykrx_unavailable(monkeypatch) -> None:
    """pykrx 미설치 + 브로커 없음이면 _fetch_intraday_prices 가 빈 딕셔너리를 반환한다."""
    import builtins
    real_import = builtins.__import__

    def _block_pykrx(name, *args, **kwargs):
        if name == "pykrx" or name.startswith("pykrx."):
            raise ImportError("pykrx not available")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _block_pykrx)
    result = OperationalPipeline()._fetch_intraday_prices(["005930", "000660"])
    assert result == {}


def test_fetch_intraday_prices_prefers_broker_over_pykrx(monkeypatch) -> None:
    """브로커 실시간 시세가 있으면 pykrx(15분 지연)를 호출하지 않는다."""
    pipeline = OperationalPipeline()
    broker = MockBroker(price_feed={"AAAA": 9_000, "BBBB": 5_000})
    called: list[list[str]] = []
    monkeypatch.setattr(
        OperationalPipeline, "_fetch_intraday_prices_pykrx",
        staticmethod(lambda tickers: called.append(tickers) or {}),
    )

    prices = pipeline._fetch_intraday_prices(["AAAA", "BBBB"], broker=broker)

    assert prices == {"AAAA": 9_000, "BBBB": 5_000}
    assert called == []  # 전 종목이 실시간으로 채워져 폴백 불필요


def test_fetch_intraday_prices_falls_back_to_pykrx_for_missing_tickers(monkeypatch) -> None:
    """브로커가 못 준 종목만 pykrx로 보완하고, 조회된 종목은 덮어쓰지 않는다."""
    pipeline = OperationalPipeline()
    broker = MockBroker(price_feed={"AAAA": 9_000})
    called: list[list[str]] = []

    def _fake_pykrx(tickers):
        called.append(tickers)
        return {"BBBB": 5_000}

    monkeypatch.setattr(
        OperationalPipeline, "_fetch_intraday_prices_pykrx", staticmethod(_fake_pykrx),
    )

    prices = pipeline._fetch_intraday_prices(["AAAA", "BBBB"], broker=broker)

    assert prices == {"AAAA": 9_000, "BBBB": 5_000}
    assert called == [["BBBB"]]  # 실시간으로 못 받은 종목만 폴백 조회


def test_fetch_intraday_prices_survives_broker_error(monkeypatch) -> None:
    """브로커 시세 조회가 터져도 pykrx 폴백으로 값을 채운다(손절 판단 유지)."""
    pipeline = OperationalPipeline()
    broker = MockBroker(price_feed={"AAAA": 9_000})
    monkeypatch.setattr(
        broker, "get_current_prices",
        lambda _tickers: (_ for _ in ()).throw(BrokerAdapterError("quote api down")),
    )
    monkeypatch.setattr(
        OperationalPipeline, "_fetch_intraday_prices_pykrx",
        staticmethod(lambda tickers: {"AAAA": 8_800}),
    )

    assert pipeline._fetch_intraday_prices(["AAAA"], broker=broker) == {"AAAA": 8_800}


def test_sync_broker_updates_price_feed_when_exit_monitor_active(monkeypatch) -> None:
    """exit_monitor_active 시 보유 종목의 현재가가 broker price_feed 에 반영된다."""
    import maps.ops.scheduler as scheduler_module

    engine, factory = _session_factory()
    settings = MapsSettings(
        maps_broker_mode="mock",
        maps_data_provider="mock",
        maps_live_trading_enabled=True,
    )
    pipeline = OperationalPipeline(settings=settings, session_factory=factory)

    # MockBroker 에 보유 포지션 주입
    held_broker = MockBroker(initial_cash=900_000, price_feed={"AAAA": 10_000})
    held_broker.place_order(Order(
        strategy_id="s1", ticker="AAAA", side=OrderSide.BUY,
        order_type=OrderType.MARKET, quantity=10,
    ))

    monkeypatch.setattr(scheduler_module, "get_broker", lambda _mode: held_broker)

    # is_market_open → True, _fetch_intraday_prices → 현재가 9,000원 반환
    monkeypatch.setattr(held_broker, "is_market_open", lambda: True)
    monkeypatch.setattr(
        OperationalPipeline, "_fetch_intraday_prices",
        lambda self, tickers, broker=None: {"AAAA": 9_000},
    )

    pipeline.sync_broker_state(dt.date(2026, 5, 5))

    assert held_broker._price_feed.get("AAAA") == 9_000

    engine.dispose()
    Base.metadata.drop_all(engine)


# ---------------------------------------------------------------------------
# H-3: OHLCV 데이터 신선도 검증
# ---------------------------------------------------------------------------

def test_is_data_fresh_returns_true_when_data_is_recent() -> None:
    """최신 OHLCV 날짜가 expected_ohlcv_date 이상이고 티커 수가 MIN_FRESH_TICKERS 이상이면 True."""
    engine, factory = _session_factory()
    ref_date = dt.date(2026, 5, 5)
    pipeline = OperationalPipeline(session_factory=factory)
    db = factory()
    try:
        for i in range(OperationalPipeline._MIN_FRESH_TICKERS):
            db.add(HistoricalOHLCV(
                ticker=f"{i:06d}", date=ref_date,
                open=10_000, high=10_000, low=10_000, close=10_000, volume=100_000,
            ))
        db.commit()
        fresh, latest, expected = pipeline._is_data_fresh(db, ref_date)
        assert fresh is True
        assert latest == ref_date
        assert expected <= ref_date
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_is_data_fresh_returns_false_when_data_is_stale() -> None:
    """최신 OHLCV 날짜가 expected_ohlcv_date 보다 오래됐으면 False."""
    engine, factory = _session_factory()
    ref_date = dt.date(2026, 5, 5)
    stale_date = ref_date - dt.timedelta(days=6)
    pipeline = OperationalPipeline(session_factory=factory)
    db = factory()
    try:
        db.add(HistoricalOHLCV(
            ticker="AAAA", date=stale_date,
            open=10_000, high=10_000, low=10_000, close=10_000, volume=100_000,
        ))
        db.commit()
        fresh, latest, expected = pipeline._is_data_fresh(db, ref_date)
        assert fresh is False
        assert latest == stale_date
        assert expected > stale_date
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_is_data_fresh_returns_false_when_too_few_tickers() -> None:
    """날짜가 최신이어도 MIN_FRESH_TICKERS 미만이면 False (부분 수집 감지)."""
    engine, factory = _session_factory()
    ref_date = dt.date(2026, 5, 5)
    pipeline = OperationalPipeline(session_factory=factory)
    db = factory()
    try:
        for i in range(OperationalPipeline._MIN_FRESH_TICKERS - 1):
            db.add(HistoricalOHLCV(
                ticker=f"{i:06d}", date=ref_date,
                open=10_000, high=10_000, low=10_000, close=10_000, volume=100_000,
            ))
        db.commit()
        fresh, latest, _expected = pipeline._is_data_fresh(db, ref_date)
        assert fresh is False
        assert latest == ref_date
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_is_data_fresh_returns_false_when_no_ohlcv() -> None:
    """OHLCV 데이터가 전혀 없으면 False 를 반환한다."""
    engine, factory = _session_factory()
    pipeline = OperationalPipeline(session_factory=factory)
    db = factory()
    try:
        fresh, latest, _expected = pipeline._is_data_fresh(db, dt.date(2026, 5, 5))
        assert fresh is False
        assert latest is None
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_order_cycle_skips_buy_when_ohlcv_is_stale(monkeypatch) -> None:
    """OHLCV 데이터가 5일 이상 오래됐을 때 매수 주문이 전량 스킵된다."""
    import maps.ops.scheduler as scheduler_module

    engine, factory = _session_factory()
    settings = MapsSettings(
        maps_broker_mode="mock",
        maps_data_provider="mock",
        maps_live_trading_enabled=True,
    )
    pipeline = OperationalPipeline(settings=settings, session_factory=factory)
    _force_entry_signal(monkeypatch)

    ref_date = dt.date(2026, 5, 8)
    stale_date = ref_date - dt.timedelta(days=7)  # 7일 전 데이터 → 신선도 실패

    db = factory()
    try:
        # 오래된 OHLCV 데이터만 존재
        db.add(HistoricalOHLCV(
            ticker="AAAA", date=stale_date,
            open=10_000, high=10_000, low=10_000, close=10_000, volume=100_000,
        ))
        db.add(CandidateSnapshot(
            ref_date=ref_date, strategy_id="pullback_v3", ticker="AAAA",
            name="AAAA", market="KOSPI", factor_score=90, trend_strength=80,
            ts_bucket="S5", final_score=95, weekly_pass=True,
        ))
        db.add(PromotionHistory(
            strategy_id="pullback_v3", from_stage="mock_candidate",
            to_stage="live_candidate", tradeability_score=70, passed=True,
            evaluated_at=dt.datetime(2026, 5, 8, 8, 0),
        ))
        db.commit()
    finally:
        db.close()

    run = pipeline.run_order_cycle(ref_date)

    assert run.status == "success"
    assert run.details["submitted_buy_orders"] == 0, "데이터 오래됨 → 매수 0건"

    engine.dispose()
    Base.metadata.drop_all(engine)


# ---------------------------------------------------------------------------
# M-1: 후보 스냅샷 저장 시 TrendStrengthCalculator 통합
# ---------------------------------------------------------------------------

def test_save_candidate_snapshot_uses_real_trend_strength() -> None:
    """100봉 이상의 OHLCV 데이터가 있으면 trend_strength 가 50.0 이 아닌 실제 계산값으로 저장된다.

    final_score = 0.6 * factor_score + 0.4 * trend_strength 공식 검증.
    """
    from maps.data.security_repo import Security
    import datetime as dt_lib

    engine, factory = _session_factory()
    db = factory()
    ref_date = dt.date(2026, 5, 5)

    try:
        # 상승 추세 OHLCV 120봉 삽입 (TrendStrengthCalculator min_bars=100 충족)
        base_price = 10_000
        for i in range(120):
            day = ref_date - dt.timedelta(days=120 - i)
            price = base_price + i * 50  # 매일 50원씩 상승
            db.add(HistoricalOHLCV(
                ticker="AAAA",
                date=day,
                open=price,
                high=price + 100,
                low=price - 50,
                close=price,
                volume=500_000,
            ))
        db.commit()
    finally:
        db.close()

    settings = MapsSettings(
        maps_broker_mode="mock",
        maps_data_provider="mock",
        maps_live_trading_enabled=False,
    )
    pipeline = OperationalPipeline(settings=settings, session_factory=factory)

    # Security 객체 직접 생성 (turnover_cache 주입)
    stock = Security(
        ticker="AAAA",
        name="테스트종목",
        market="KOSPI",
        security_type="STOCK",
        turnover_cache={ref_date: 1_000_000_000},  # 10억 거래대금
    )

    db2 = factory()
    try:
        pipeline._save_candidate_snapshot(db2, ref_date, "pullback_v3", [stock])
        snap = db2.query(CandidateSnapshot).filter(CandidateSnapshot.ticker == "AAAA").first()

        assert snap is not None
        # 상승 추세 → trend_strength 가 50.0 이 아니어야 함
        assert snap.trend_strength != 50.0, "실제 OHLCV가 있으면 50.0 이 아닌 실제 값이어야 함"
        # final_score = 0.6 * factor_score + 0.4 * trend_strength
        expected_final = round(0.6 * snap.factor_score + 0.4 * snap.trend_strength, 2)
        assert snap.final_score == expected_final, (
            f"final_score={snap.final_score} ≠ blend({snap.factor_score}, {snap.trend_strength})={expected_final}"
        )
        # 단독 종목이므로 factor_score = 100.0
        assert snap.factor_score == pytest.approx(100.0)
    finally:
        db2.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_save_candidate_snapshot_falls_back_when_insufficient_ohlcv() -> None:
    """OHLCV 데이터가 부족하면 trend_strength=50.0, ts_bucket='S3' 으로 저장된다."""
    from maps.data.security_repo import Security

    engine, factory = _session_factory()
    db = factory()
    ref_date = dt.date(2026, 5, 5)

    try:
        # 10봉만 삽입 (min_bars=100 미만)
        for i in range(10):
            day = ref_date - dt.timedelta(days=10 - i)
            db.add(HistoricalOHLCV(
                ticker="BBBB", date=day,
                open=5_000, high=5_100, low=4_900, close=5_000, volume=100_000,
            ))
        db.commit()
    finally:
        db.close()

    settings = MapsSettings(
        maps_broker_mode="mock",
        maps_data_provider="mock",
        maps_live_trading_enabled=False,
    )
    pipeline = OperationalPipeline(settings=settings, session_factory=factory)

    stock = Security(
        ticker="BBBB",
        name="소형주",
        market="KOSDAQ",
        security_type="STOCK",
        turnover_cache={ref_date: 500_000_000},
    )

    db2 = factory()
    try:
        pipeline._save_candidate_snapshot(db2, ref_date, "pullback_v3", [stock])
        snap = db2.query(CandidateSnapshot).filter(CandidateSnapshot.ticker == "BBBB").first()

        assert snap is not None
        assert snap.trend_strength == pytest.approx(50.0), "데이터 부족 시 기본값 50.0"
        assert snap.ts_bucket == "S3", "데이터 부족 시 기본 버킷 S3"
    finally:
        db2.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_generate_candidates_saves_snapshots_even_when_regime_blocks_entry() -> None:
    """weak 장세에서도 스냅샷은 저장되고(관측 지속) 진입만 차단되는지 검증."""
    engine, factory = _session_factory()
    settings = MapsSettings(
        maps_broker_mode="mock",
        maps_data_provider="mock",
        maps_live_trading_enabled=False,
        maps_market_regime_override="weak",
    )
    pipeline = OperationalPipeline(settings=settings, session_factory=factory)

    pipeline.collect_data()
    run = pipeline.generate_candidates()

    assert run.status == "success"
    assert run.details["saved_count"] > 0
    blocked_ids = {b["strategy_id"] for b in run.details["strategies_blocked"]}
    assert "pullback_v3" in blocked_ids  # weak는 pullback preferred_regimes에 없음
    assert "pullback_v3" not in run.details["strategies_updated"]

    db = factory()
    try:
        # 차단된 전략의 스냅샷도 저장되어야 한다.
        assert (
            db.query(CandidateSnapshot)
            .filter(CandidateSnapshot.strategy_id == "pullback_v3")
            .count()
            > 0
        )
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_order_cycle_skips_buy_when_regime_not_preferred(monkeypatch) -> None:
    """스냅샷이 존재해도 주문 시점 장세가 preferred_regimes에 없으면 매수를 스킵한다."""
    engine, factory = _session_factory()
    settings = MapsSettings(
        maps_broker_mode="mock",
        maps_data_provider="mock",
        maps_live_trading_enabled=True,
        maps_market_regime_override="weak",
    )
    pipeline = OperationalPipeline(settings=settings, session_factory=factory)
    _force_entry_signal(monkeypatch)
    ref_date = dt.date(2026, 5, 5)

    db = factory()
    try:
        _add_fresh_ohlcv(db, ref_date, ticker="AAAA", close=10_000.0)
        db.add(CandidateSnapshot(
            ref_date=ref_date, strategy_id="pullback_v3", ticker="AAAA",
            name="AAAA", market="KOSPI", factor_score=90, trend_strength=80,
            ts_bucket="S5", final_score=95, weekly_pass=True,
        ))
        db.add(PromotionHistory(
            strategy_id="pullback_v3", from_stage="mock_candidate",
            to_stage="live_candidate", tradeability_score=70, passed=True,
            evaluated_at=dt.datetime(2026, 5, 5, 8, 0),
        ))
        db.commit()
    finally:
        db.close()

    run = pipeline.run_order_cycle(ref_date)

    assert run.status == "success"
    assert run.details["submitted_buy_orders"] == 0

    db = factory()
    try:
        assert db.query(OrderLog).filter(OrderLog.side == "buy").count() == 0
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


# ── job_run_log 영속 (SCR-21 배치 모니터) ────────────────────────────────────

def _make_scheduler():
    engine, factory = _session_factory()
    settings = MapsSettings(
        maps_broker_mode="mock",
        maps_data_provider="mock",
        maps_scheduler_enabled=False,
    )
    pipeline = OperationalPipeline(settings=settings, session_factory=factory)
    return engine, factory, MapsOperationalScheduler(settings=settings, pipeline=pipeline)


def test_run_once_persists_success_to_job_run_log() -> None:
    """잡 성공이 job_run_log에 남는다 — 인메모리 _last_runs와 달리 재시작에 생존."""
    engine, factory, scheduler = _make_scheduler()
    try:
        scheduler.run_once("validation")

        db = factory()
        try:
            rows = db.query(JobRunLog).all()
            assert len(rows) == 1
            assert rows[0].name == "validation"
            assert rows[0].status == "success"
            assert rows[0].ref_date == dt.date.today()
            assert rows[0].finished_at is not None
        finally:
            db.close()
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_run_once_persists_failure_to_job_run_log(monkeypatch) -> None:
    """잡 실패가 DB에 남는다 — 기존에는 로그·Slack뿐이라 재시작 후 소실됐다."""
    from maps.ops.scheduler import JobRun

    engine, factory, scheduler = _make_scheduler()
    try:
        failed = JobRun(
            name="validation",
            status="failed",
            started_at=dt.datetime.now(dt.timezone.utc),
            finished_at=dt.datetime.now(dt.timezone.utc),
            message="boom",
        )
        monkeypatch.setattr(scheduler._pipeline, "run_validation", lambda: failed)

        run = scheduler.run_once("validation")
        assert run.status == "failed"

        db = factory()
        try:
            row = db.query(JobRunLog).one()
            assert row.status == "failed"
            assert row.message == "boom"
        finally:
            db.close()
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_broker_sync_success_is_not_persisted_but_failure_is(monkeypatch) -> None:
    """broker_sync 성공은 60초마다라 노이즈 — 하트비트는 collection_log가 담당."""
    from maps.ops.scheduler import JobRun

    engine, factory, scheduler = _make_scheduler()
    try:
        scheduler.run_once("broker_sync")
        db = factory()
        try:
            assert db.query(JobRunLog).count() == 0
        finally:
            db.close()

        failed = JobRun(
            name="broker_sync",
            status="failed",
            started_at=dt.datetime.now(dt.timezone.utc),
            message="sync down",
        )
        monkeypatch.setattr(scheduler._pipeline, "sync_broker_state", lambda: failed)
        scheduler.run_once("broker_sync")

        db = factory()
        try:
            row = db.query(JobRunLog).one()
            assert row.name == "broker_sync"
            assert row.status == "failed"
        finally:
            db.close()
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_persist_failure_does_not_break_job(monkeypatch) -> None:
    """job_run_log 기록 실패(DB 장애 등)가 잡 결과를 죽이면 안 된다."""
    engine, factory, scheduler = _make_scheduler()
    try:
        run_ok = scheduler.run_once("validation")  # 워밍업: 정상 경로 확인
        assert run_ok.status == "success"

        def _broken_factory():
            raise RuntimeError("db down")

        monkeypatch.setattr(scheduler._pipeline, "_session_factory", _broken_factory)
        # _persist_run 내부 예외가 삼켜지고 run은 그대로 반환돼야 한다
        run = scheduler._record("validation", lambda: run_ok)
        assert run.status == "success"
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_eod_cleanup_purges_job_run_log_older_than_90_days() -> None:
    engine, factory, scheduler = _make_scheduler()
    try:
        today = dt.date.today()
        db = factory()
        try:
            for age_days in (120, 30):
                db.add(
                    JobRunLog(
                        name="validation",
                        status="success",
                        ref_date=today - dt.timedelta(days=age_days),
                        started_at=dt.datetime.now(dt.timezone.utc),
                    )
                )
            db.commit()
        finally:
            db.close()

        run = scheduler.run_once("eod_cleanup")
        assert run.status == "success"
        assert run.details["job_run_log_purged"] == 1

        db = factory()
        try:
            remaining = {
                row.ref_date
                for row in db.query(JobRunLog).filter(JobRunLog.name == "validation").all()
            }
            assert today - dt.timedelta(days=120) not in remaining
            assert today - dt.timedelta(days=30) in remaining
        finally:
            db.close()
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


# ── 자동 강등 E2E (검증 잡 → 강등 행 → 주문 자격 상실 → Slack) ─────────────

def test_validation_auto_demotes_after_consecutive_low_scores(monkeypatch) -> None:
    """점수 연속 미달 시 검증 잡이 mock→research 강등을 기록·알림해야 한다."""
    from types import SimpleNamespace

    import maps.promotion.gate as gate_module

    monkeypatch.setattr(
        gate_module, "get_settings",
        lambda: SimpleNamespace(maps_demotion_consecutive_evals=3),
    )

    class RecordingNotifier:
        def __init__(self) -> None:
            self.sent = []

        def send(self, notification) -> bool:
            self.sent.append(notification)
            return True

        def send_job_failed(self, *args, **kwargs) -> bool:
            return True

    notifier = RecordingNotifier()
    engine, factory = _session_factory()
    settings = MapsSettings(
        maps_broker_mode="mock",
        maps_data_provider="mock",
        maps_live_trading_enabled=False,
    )
    pipeline = OperationalPipeline(
        settings=settings, session_factory=factory, notifier=notifier
    )
    ref_date = dt.date(2026, 8, 3)

    db = factory()
    try:
        # 과거 승격 행 (윈도 밖) → 현재 단계 mock_candidate
        db.add(PromotionHistory(
            strategy_id="pullback_v3", from_stage="research", to_stage="mock_candidate",
            tradeability_score=71.0, passed=True,
            evaluated_at=dt.datetime.now() - dt.timedelta(days=30),
        ))
        # 직전 2회 연속 미달 (오늘 평가가 3회째)
        for days_ago in (2, 1):
            db.add(PromotionHistory(
                strategy_id="pullback_v3", from_stage="mock_candidate",
                to_stage="mock_candidate", tradeability_score=40.0, passed=False,
                evaluated_at=dt.datetime.now() - dt.timedelta(days=days_ago),
            ))
        # 낮은 점수를 만드는 최신 메트릭 (점수 ≈ 21 < 50)
        db.add(CandidateSnapshot(
            ref_date=ref_date, strategy_id="pullback_v3", ticker="AAAA", name="AAAA",
            market="KOSPI", factor_score=50, trend_strength=50, ts_bucket="S3",
            final_score=50, weekly_pass=True,
        ))
        db.add(ParameterPlateauResults(
            strategy_id="pullback_v3", run_date=ref_date, total_combinations=10,
            positive_combinations=4, positive_ratio=0.4, grade="D",
        ))
        db.add(MonteCarloSequenceResults(
            strategy_id="pullback_v3", strategy_group="pullback_short", run_date=ref_date,
            n_simulations=100, mdd_p95=0.16, mdd_limit=0.18, mc_within_limit=True,
        ))
        db.add(WalkForwardResults(
            strategy_id="pullback_v3", run_date=ref_date, n_folds=3,
            sharpe_mean=0.2, sharpe_std=0.3, negative_folds=1, mean_g2p=0.4, passed=False,
        ))
        db.commit()
    finally:
        db.close()

    db = factory()
    try:
        result = pipeline._evaluate_promotions(db, ref_date)

        assert result["demoted"] == ["pullback_v3"]
        demotion = (
            db.query(PromotionHistory)
            .filter(PromotionHistory.passed.is_(True), PromotionHistory.to_stage == "research")
            .one()
        )
        assert demotion.from_stage == "mock_candidate"
        # 주문 자격: 최신 passed=True 행 기준 research → eligible_stages 탈락
        assert pipeline._latest_promotions(db)["pullback_v3"] == "research"
        # Slack WARN 발송
        assert len(notifier.sent) == 1
        assert notifier.sent[0].level == "WARN"
        assert "pullback_v3" in notifier.sent[0].title
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()
