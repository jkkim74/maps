from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import maps.common.models  # noqa: F401
from maps.common.db import Base
from maps.common.models import CandidateSnapshot, HistoricalOHLCV, OrderLog
from maps.common.settings import MapsSettings
from maps.execution.broker_adapter import Order, OrderSide, OrderType
from maps.execution.mock_broker import MockBroker
from maps.execution.order_manager import OrderManager
from maps.ops.scheduler import OperationalPipeline, plan_exit_decision


# ── 순수 판정 함수 ────────────────────────────────────────────────────────────

_BASE = dict(
    current_price=10_000.0,
    entry_price=10_000.0,
    hwm=10_000.0,
    emergency_stop=None,
    technical_stop=None,
    target=None,
    fallback_stop=None,
    strategy_exit=False,
    trail_activate_pct=0.05,
    trail_stop_pct=0.08,
)


def _decide(**over):
    return plan_exit_decision(**{**_BASE, **over})


def test_emergency_stop_triggers_first() -> None:
    # 긴급손절이 목표보다 우선
    assert _decide(current_price=8_000, emergency_stop=8_500, target=7_000) == (True, "emergency_stop")


def test_plan_technical_stop_used_over_fallback() -> None:
    assert _decide(current_price=9_400, technical_stop=9_500, fallback_stop=9_000) == (True, "plan_stop")


def test_fallback_stop_when_no_technical_stop() -> None:
    assert _decide(current_price=8_900, technical_stop=None, fallback_stop=9_000) == (True, "stop_loss")


def test_trailing_stop_triggers_after_activation() -> None:
    # 고점 13,000(진입 10,000 대비 +30%, 활성) → 고점 대비 -8% = 11,960 이하면 청산
    assert _decide(current_price=11_500, entry_price=10_000, hwm=13_000) == (True, "trailing_stop")


def test_trailing_not_triggered_before_activation() -> None:
    # 고점이 진입 대비 +5% 미만이면 트레일링 미활성
    assert _decide(current_price=10_200, entry_price=10_000, hwm=10_300) == (False, None)


def test_take_profit_at_target() -> None:
    assert _decide(current_price=11_600, entry_price=10_000, hwm=11_600, target=11_500) == (True, "take_profit")


def test_strategy_exit_last() -> None:
    assert _decide(strategy_exit=True) == (True, "strategy_exit")


def test_no_exit_when_nothing_hit() -> None:
    assert _decide(current_price=10_400, entry_price=10_000, hwm=10_400, target=12_000, fallback_stop=9_500) == (False, None)


def test_invalid_price_no_exit() -> None:
    assert _decide(current_price=0, emergency_stop=9_000) == (False, None)


# ── DB 헬퍼 ───────────────────────────────────────────────────────────────────

def _factory():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return engine, sessionmaker(autocommit=False, autoflush=False, bind=engine)


def test_entry_trade_plan_picks_latest_on_or_before_entry() -> None:
    engine, factory = _factory()
    db = factory()
    try:
        pipeline = OperationalPipeline(session_factory=factory)
        for d, target in [(dt.date(2026, 5, 1), 100.0), (dt.date(2026, 5, 4), 200.0), (dt.date(2026, 5, 10), 300.0)]:
            db.add(CandidateSnapshot(
                ref_date=d, strategy_id="pullback_v3", ticker="AAAA", name="AAAA", market="KOSPI",
                factor_score=1, trend_strength=1, ts_bucket="S3", final_score=1, weekly_pass=True,
                trading_target=target,
            ))
        db.commit()
        plan = pipeline._entry_trade_plan(db, "AAAA", "pullback_v3", dt.date(2026, 5, 5))
        assert plan is not None and plan.trading_target == 200.0  # 5/4 (≤ 5/5 최신)
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_high_water_mark_from_ohlcv() -> None:
    engine, factory = _factory()
    db = factory()
    try:
        pipeline = OperationalPipeline(session_factory=factory)
        for d, high in [(dt.date(2026, 5, 3), 9_000), (dt.date(2026, 5, 5), 13_000), (dt.date(2026, 5, 6), 11_000)]:
            db.add(HistoricalOHLCV(ticker="AAAA", date=d, open=high, high=high, low=high, close=high, volume=1))
        db.commit()
        hwm = pipeline._high_water_mark(db, "AAAA", dt.date(2026, 5, 4), fallback=0.0)
        assert hwm == 13_000.0  # 5/4 이후 최고가 (5/3의 9,000 제외)
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


# ── 통합: _submit_exit_orders ────────────────────────────────────────────────

def _held_pipeline(monkeypatch, *, plan_enabled: bool, target: float | None, price: float):
    engine, factory = _factory()
    settings = MapsSettings(
        maps_broker_mode="mock", maps_data_provider="mock", maps_live_trading_enabled=True,
        maps_plan_based_exits_enabled=plan_enabled,
    )
    pipeline = OperationalPipeline(settings=settings, session_factory=factory)
    db = factory()
    broker = MockBroker(initial_cash=900_000, price_feed={"AAAA": price})
    broker.place_order(Order(
        strategy_id="pullback_v3", ticker="AAAA", side=OrderSide.BUY,
        order_type=OrderType.MARKET, quantity=10,
    ))
    db.add(OrderLog(
        order_id="buy-1", strategy_id="pullback_v3", ticker="AAAA", side=OrderSide.BUY.value,
        qty=10, order_price=10_000, fill_price=10_000, status="filled",
        created_at=dt.datetime(2026, 5, 4, 9, 0),
    ))
    db.add(CandidateSnapshot(
        ref_date=dt.date(2026, 5, 4), strategy_id="pullback_v3", ticker="AAAA", name="AAAA",
        market="KOSPI", factor_score=1, trend_strength=1, ts_bucket="S3", final_score=1,
        weekly_pass=True, trading_target=target,
    ))
    db.commit()
    # 전략 신호 계산은 OHLCV 의존 → 단순화: exit_signal 없음, atr 없음
    monkeypatch.setattr(
        OperationalPipeline, "_latest_strategy_signal",
        staticmethod(lambda *a, **k: SimpleNamespace(entry_signal=False, exit_signal=False, close=0.0, atr14=None)),
    )
    manager = OrderManager(broker=broker, risk=pipeline._make_risk_manager(broker, db), db=db)
    return pipeline, db, broker, manager, engine


def test_plan_exit_takes_profit_when_enabled(monkeypatch) -> None:
    pipeline, db, broker, manager, engine = _held_pipeline(
        monkeypatch, plan_enabled=True, target=11_500, price=12_000
    )
    try:
        submitted, _skipped, exit_tickers = pipeline._submit_exit_orders(
            db=db, broker=broker, manager=manager, ref_date=dt.date(2026, 5, 5),
        )
        assert submitted == 1
        assert "AAAA" in exit_tickers
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_no_exit_when_gate_off_and_no_stop(monkeypatch) -> None:
    # 게이트 OFF + 손절/전략신호 없음 → 목표 도달이어도 청산하지 않음(기존 동작)
    pipeline, db, broker, manager, engine = _held_pipeline(
        monkeypatch, plan_enabled=False, target=11_500, price=12_000
    )
    try:
        submitted, _skipped, exit_tickers = pipeline._submit_exit_orders(
            db=db, broker=broker, manager=manager, ref_date=dt.date(2026, 5, 5),
        )
        assert submitted == 0
        assert exit_tickers == set()
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()
