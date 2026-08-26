from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import maps.common.models  # noqa: F401
from maps.common.db import Base
from maps.common.models import (
    AnalysisPick,
    CandidateSnapshot,
    HistoricalOHLCV,
    HoldingRegimeAudit,
    MarketRegimeLog,
    OrderLog,
)
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


def _overlay_context(*, snapshot_id: int = 1, regime: str = "mixed") -> dict:
    return {
        "version": 1,
        "origin": "live",
        "candidate": {"snapshot_id": snapshot_id, "ref_date": "2026-08-24"},
        "market": {
            "ref_date": "2026-08-24",
            "source": "order_cycle",
            "regime": regime,
            "weekly_trend": "pass",
            "vol_regime": "normal",
        },
    }


def _overlay_env(*, mode: str = "shadow"):
    engine, factory = _factory()
    settings = MapsSettings(
        _env_file=None,
        maps_live_trading_enabled=False,
        maps_holding_regime_overlay_mode=mode,
        maps_holding_regime_max_age_days=3,
        maps_strategy_trade_enabled=False,
    )
    pipeline = OperationalPipeline(settings=settings, session_factory=factory)
    db = factory()
    candidate = CandidateSnapshot(
        ref_date=dt.date(2026, 8, 24),
        strategy_id="donchian_v2",
        ticker="AAAA",
        name="AAAA",
        market="KOSPI",
        factor_score=1,
        trend_strength=1,
        ts_bucket="S3",
        final_score=1,
        weekly_pass=True,
    )
    db.add(candidate)
    db.flush()
    broker = MockBroker(initial_cash=900_000, price_feed={"AAAA": 10_000})
    broker.place_order(Order(
        strategy_id="donchian_v2",
        ticker="AAAA",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=10,
    ))
    db.add(OrderLog(
        order_id="overlay-buy-1",
        strategy_id="donchian_v2",
        ticker="AAAA",
        side="buy",
        qty=10,
        order_price=10_000,
        fill_price=10_000,
        fill_qty=10,
        status="filled",
        decision_context=_overlay_context(snapshot_id=candidate.id),
        created_at=dt.datetime(2026, 8, 24, 9, 0),
    ))
    for day, source in [(24, "candidate_generation"), (25, "candidate_generation"), (26, "order_cycle")]:
        db.add(MarketRegimeLog(
            ref_date=dt.date(2026, 8, day),
            raw_regime="mixed",
            applied_regime="mixed",
            weekly_trend="fail",
            vol_regime="high",
            source=source,
        ))
    db.commit()
    return pipeline, db, broker, engine


def test_shadow_overlay_uses_close_rows_and_never_submits_sell() -> None:
    pipeline, db, broker, engine = _overlay_env()
    try:
        pipeline._record_holding_regime_shadow(
            db=db,
            broker=broker,
            ref_date=dt.date(2026, 8, 26),
            exclude_tickers=set(),
        )

        audit = db.query(HoldingRegimeAudit).one()
        assert audit.action == "exit"
        assert audit.reason_code == "CONFIRMED_ADVERSE_REGIME"
        assert audit.details["current"]["ref_date"] == "2026-08-25"
        assert audit.details["previous"]["ref_date"] == "2026-08-24"
        assert db.query(OrderLog).filter(OrderLog.side == "sell").count() == 0
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_shadow_overlay_does_not_reuse_entry_after_later_filled_sell() -> None:
    pipeline, db, broker, engine = _overlay_env()
    try:
        db.add(OrderLog(
            order_id="overlay-sell-1",
            strategy_id="donchian_v2",
            ticker="AAAA",
            side="sell",
            qty=10,
            order_price=9_000,
            fill_price=9_000,
            fill_qty=10,
            status="filled",
            created_at=dt.datetime(2026, 8, 25, 9, 0),
        ))
        db.commit()

        pipeline._record_holding_regime_shadow(
            db=db,
            broker=broker,
            ref_date=dt.date(2026, 8, 26),
            exclude_tickers=set(),
        )

        audit = db.query(HoldingRegimeAudit).one()
        assert audit.action == "hold"
        assert audit.reason_code == "POSITION_PROVENANCE_AMBIGUOUS"
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_shadow_overlay_unverified_fill_fails_open() -> None:
    pipeline, db, broker, engine = _overlay_env()
    try:
        entry = db.query(OrderLog).filter(OrderLog.side == "buy").one()
        entry.fill_qty = 0
        db.commit()

        pipeline._record_holding_regime_shadow(
            db=db,
            broker=broker,
            ref_date=dt.date(2026, 8, 26),
            exclude_tickers=set(),
        )

        audit = db.query(HoldingRegimeAudit).one()
        assert audit.action == "hold"
        assert audit.reason_code == "ENTRY_FILL_UNVERIFIED"
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_shadow_overlay_rejects_candidate_context_without_identity() -> None:
    pipeline, db, broker, engine = _overlay_env()
    try:
        entry = db.query(OrderLog).filter(OrderLog.side == "buy").one()
        entry.decision_context = {**entry.decision_context, "candidate": {}}
        db.commit()

        pipeline._record_holding_regime_shadow(
            db=db,
            broker=broker,
            ref_date=dt.date(2026, 8, 26),
            exclude_tickers=set(),
        )

        audit = db.query(HoldingRegimeAudit).one()
        assert audit.action == "hold"
        assert audit.reason_code == "ENTRY_CONTEXT_INVALID"
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_shadow_overlay_rejects_unknown_candidate_snapshot() -> None:
    pipeline, db, broker, engine = _overlay_env()
    try:
        entry = db.query(OrderLog).filter(OrderLog.side == "buy").one()
        entry.decision_context = {
            **entry.decision_context,
            "candidate": {"snapshot_id": 999, "ref_date": "2026-08-24"},
        }
        db.commit()

        pipeline._record_holding_regime_shadow(
            db=db,
            broker=broker,
            ref_date=dt.date(2026, 8, 26),
            exclude_tickers=set(),
        )

        audit = db.query(HoldingRegimeAudit).one()
        assert audit.action == "hold"
        assert audit.reason_code == "ENTRY_CONTEXT_INVALID"
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_shadow_overlay_rejects_future_entry_market_date() -> None:
    pipeline, db, broker, engine = _overlay_env()
    try:
        entry = db.query(OrderLog).filter(OrderLog.side == "buy").one()
        entry.decision_context = {
            **entry.decision_context,
            "market": {**entry.decision_context["market"], "ref_date": "2026-08-27"},
        }
        db.commit()

        pipeline._record_holding_regime_shadow(
            db=db,
            broker=broker,
            ref_date=dt.date(2026, 8, 26),
            exclude_tickers=set(),
        )

        audit = db.query(HoldingRegimeAudit).one()
        assert audit.action == "hold"
        assert audit.reason_code == "ENTRY_CONTEXT_INVALID"
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_shadow_overlay_excludes_bought_analysis_pick_even_when_feature_off() -> None:
    pipeline, db, broker, engine = _overlay_env()
    try:
        db.add(AnalysisPick(
            ref_date=dt.date(2026, 8, 24),
            ticker="AAAA",
            name="AAAA",
            source="manual",
            state="BOUGHT",
            strategy_trade_enabled=True,
        ))
        db.commit()

        pipeline._record_holding_regime_shadow(
            db=db,
            broker=broker,
            ref_date=dt.date(2026, 8, 26),
            exclude_tickers=set(),
        )

        assert db.query(HoldingRegimeAudit).count() == 0
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_off_overlay_records_nothing() -> None:
    pipeline, db, broker, engine = _overlay_env(mode="off")
    try:
        pipeline._record_holding_regime_shadow(
            db=db,
            broker=broker,
            ref_date=dt.date(2026, 8, 26),
            exclude_tickers=set(),
        )

        assert db.query(HoldingRegimeAudit).count() == 0
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_same_shadow_inputs_do_not_rewrite_daily_audit() -> None:
    pipeline, db, broker, engine = _overlay_env()
    try:
        pipeline._record_holding_regime_shadow(
            db=db,
            broker=broker,
            ref_date=dt.date(2026, 8, 26),
            exclude_tickers=set(),
        )
        first = db.query(HoldingRegimeAudit).one()
        first_updated_at = first.updated_at

        pipeline._record_holding_regime_shadow(
            db=db,
            broker=broker,
            ref_date=dt.date(2026, 8, 26),
            exclude_tickers=set(),
        )
        db.refresh(first)

        assert db.query(HoldingRegimeAudit).count() == 1
        assert first.updated_at == first_updated_at
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_shadow_audit_failure_does_not_block_existing_stop(monkeypatch, caplog) -> None:
    import maps.ops.scheduler as scheduler_module

    pipeline, db, broker, _manager, engine = _held_pipeline(
        monkeypatch,
        plan_enabled=False,
        target=None,
        price=8_000,
    )
    called = 0

    def fail_shadow(*args, **kwargs):
        nonlocal called
        called += 1
        raise RuntimeError("audit unavailable")

    try:
        entry = db.query(OrderLog).filter(OrderLog.side == "buy").one()
        entry.fill_qty = 10
        db.commit()
        monkeypatch.setattr(scheduler_module, "get_broker", lambda _mode: broker)
        monkeypatch.setattr(broker, "is_market_open", lambda: True)
        monkeypatch.setattr(broker, "get_daily_order_results", lambda: [])
        monkeypatch.setattr(pipeline, "_record_holding_regime_shadow", fail_shadow)

        run = pipeline.sync_broker_state(ref_date=dt.date(2026, 5, 5))

        assert run.status == "success"
        assert called == 1
        assert db.query(OrderLog).filter(OrderLog.side == "sell").one().exit_reason == "stop_loss"
        assert "Holding regime shadow audit failed" in caplog.text
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_shadow_overlay_records_when_live_trading_is_disabled(monkeypatch) -> None:
    import maps.ops.scheduler as scheduler_module

    pipeline, db, broker, engine = _overlay_env()
    try:
        monkeypatch.setattr(scheduler_module, "get_broker", lambda _mode: broker)
        monkeypatch.setattr(broker, "get_daily_order_results", lambda: [])

        run = pipeline.sync_broker_state(ref_date=dt.date(2026, 8, 26))

        assert run.status == "success"
        assert db.query(HoldingRegimeAudit).one().action == "exit"
        assert db.query(OrderLog).filter(OrderLog.side == "sell").count() == 0
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()
