"""Part B 전략매매 브래킷 실행 엔진 테스트 (_process_strategy_trades)."""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import maps.common.models  # noqa: F401
from maps.common.db import Base
from maps.common.models import AnalysisPick, AnalysisPickLeg, OrderLog
from maps.common.settings import MapsSettings
from maps.execution.broker_adapter import Order, OrderResult, OrderSide, OrderStatus, OrderType
from maps.execution.mock_broker import MockBroker
from maps.execution.order_manager import OrderManager
from maps.market.trading_rules import trading_days_ago
from maps.ops.scheduler import OperationalPipeline

# 픽 기준일은 today 상대값이어야 한다. 고정 날짜로 두면 신선도 가드가 들어온 뒤
# 시간이 흐르면서 전 테스트가 조용히 만료 픽을 쓰게 된다.
_TODAY = dt.date.today()


@pytest.fixture
def env():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    pipeline = OperationalPipeline(session_factory=factory)
    broker = MockBroker(initial_cash=100_000_000, price_feed={})
    db = factory()
    risk = pipeline._make_risk_manager(broker, db)
    manager = OrderManager(broker=broker, risk=risk, db=db)
    try:
        yield pipeline, broker, manager, db
    finally:
        db.close()
        engine.dispose()


def _pick(db, *, ticker="005930", buy=70000, target=80000, stop=66000, qty=10, state="ARMED"):
    p = AnalysisPick(
        ref_date=_TODAY, ticker=ticker, name=ticker, source="manual",
        buy_price=buy, target_price=target, stop_price=stop, qty=qty,
        strategy_trade_enabled=True, state=state,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _run(pipeline, broker, manager, db, picks, prices):
    broker.update_prices(prices)
    return pipeline._process_strategy_trades(db=db, broker=broker, manager=manager, picks=picks, prices=prices)


def test_incomplete_market_score_blocks_new_strategy_buy(env):
    pipeline, broker, manager, db = env
    pipeline._settings = MapsSettings(maps_score_readiness_required=True)
    pick = _pick(db)

    submitted, closed = _run(
        pipeline, broker, manager, db, [pick], {pick.ticker: 69_000}
    )

    assert (submitted, closed) == (0, 0)
    assert db.query(OrderLog).count() == 0


def test_incomplete_market_score_does_not_block_exit(env):
    pipeline, broker, manager, db = env
    pick = _pick(db)
    _run(pipeline, broker, manager, db, [pick], {pick.ticker: 69_000})
    pipeline._settings = MapsSettings(maps_score_readiness_required=True)

    submitted, closed = _run(
        pipeline, broker, manager, db, [pick], {pick.ticker: 81_000}
    )

    assert (submitted, closed) == (0, 1)
    assert pick.state == "CLOSED"


def _split_pick(db, *, ticker="005930", state="ARMED"):
    pick = AnalysisPick(
        ref_date=_TODAY,
        ticker=ticker,
        name=ticker,
        source="analyze",
        buy_price=70_000,
        target_price=80_000,
        stop_price=60_000,
        qty=30,
        trade_mode="split",
        total_budget=2_010_000,
        strategy_trade_enabled=True,
        state=state,
    )
    pick.legs = [
        AnalysisPickLeg(sequence=1, entry_price=70_000, weight_pct=30, planned_qty=9, status="PENDING"),
        AnalysisPickLeg(sequence=2, entry_price=67_000, weight_pct=30, planned_qty=9, status="PENDING"),
        AnalysisPickLeg(sequence=3, entry_price=64_000, weight_pct=40, planned_qty=12, status="PENDING"),
    ]
    db.add(pick)
    db.commit()
    db.refresh(pick)
    return pick


def _seed_leg_order(db, leg, *, status, fill_qty, order_id="leg-order-1"):
    leg.order_id = order_id
    db.add(OrderLog(
        order_id=order_id,
        strategy_id=f"strategy_trade:{leg.pick_id}:leg:{leg.sequence}",
        ticker=leg.pick.ticker,
        side="buy",
        qty=leg.planned_qty,
        order_price=leg.entry_price,
        fill_price=leg.entry_price if fill_qty else None,
        fill_qty=fill_qty,
        status=status,
    ))
    db.commit()


def test_split_submits_only_first_eligible_leg_per_cycle(env):
    pipeline, broker, manager, db = env
    pick = _split_pick(db)

    submitted, closed = _run(
        pipeline, broker, manager, db, [pick], {pick.ticker: 63_000}
    )

    assert (submitted, closed) == (1, 0)
    assert pick.legs[0].order_id
    assert pick.legs[1].order_id is None
    assert pick.legs[2].order_id is None


def test_split_waits_for_full_fill_before_next_leg(env):
    pipeline, broker, manager, db = env
    pick = _split_pick(db)
    _seed_leg_order(db, pick.legs[0], status="partially_filled", fill_qty=4)

    submitted, _closed = _run(
        pipeline, broker, manager, db, [pick], {pick.ticker: 63_000}
    )

    assert submitted == 0
    assert pick.legs[0].filled_qty == 4
    assert pick.legs[1].order_id is None


def test_split_partial_fill_sync_is_idempotent(env):
    pipeline, broker, manager, db = env
    pick = _split_pick(db)
    _seed_leg_order(db, pick.legs[0], status="partially_filled", fill_qty=4)

    _run(pipeline, broker, manager, db, [pick], {pick.ticker: 63_000})
    _run(pipeline, broker, manager, db, [pick], {pick.ticker: 63_000})

    assert pick.legs[0].filled_qty == 4


def test_dead_partial_order_retries_only_remaining_quantity(env):
    pipeline, broker, manager, db = env
    pick = _split_pick(db)
    _seed_leg_order(db, pick.legs[0], status="expired", fill_qty=4)

    submitted, _closed = _run(
        pipeline, broker, manager, db, [pick], {pick.ticker: 63_000}
    )

    assert submitted == 1
    newest = db.query(OrderLog).order_by(OrderLog.id.desc()).first()
    assert newest.qty == pick.legs[0].planned_qty - 4
    assert pick.legs[1].order_id is None


def test_split_advances_to_second_leg_on_later_cycle(env):
    pipeline, broker, manager, db = env
    pick = _split_pick(db)

    _run(pipeline, broker, manager, db, [pick], {pick.ticker: 63_000})
    submitted, _closed = _run(
        pipeline, broker, manager, db, [pick], {pick.ticker: 63_000}
    )

    assert submitted == 1
    assert pick.legs[0].status == "FILLED"
    assert pick.legs[1].order_id is not None
    assert pick.legs[2].order_id is None


def test_split_cash_shortfall_holds_without_shrinking_quantity(env):
    pipeline, _broker, _manager, db = env
    pick = _split_pick(db)
    broker = MockBroker(initial_cash=100_000, price_feed={})
    manager = OrderManager(broker=broker, risk=pipeline._make_risk_manager(broker, db), db=db)

    submitted, _closed = _run(
        pipeline, broker, manager, db, [pick], {pick.ticker: 63_000}
    )

    assert submitted == 0
    assert pick.legs[0].order_id is None
    assert db.query(OrderLog).count() == 0


def test_split_dead_partial_accumulates_retry_fill_once(env):
    pipeline, broker, manager, db = env
    pick = _split_pick(db)
    _seed_leg_order(db, pick.legs[0], status="expired", fill_qty=4)

    _run(pipeline, broker, manager, db, [pick], {pick.ticker: 63_000})
    _run(pipeline, broker, manager, db, [pick], {pick.ticker: 69_000})

    assert pick.legs[0].filled_qty == pick.legs[0].planned_qty
    assert pick.legs[0].status == "FILLED"


def test_split_exit_dominates_next_eligible_entry(env):
    pipeline, broker, manager, db = env
    pick = _split_pick(db)

    _run(pipeline, broker, manager, db, [pick], {pick.ticker: 63_000})
    submitted, closed = _run(
        pipeline, broker, manager, db, [pick], {pick.ticker: 81_000}
    )

    assert (submitted, closed) == (0, 1)
    assert pick.state == "CLOSED"
    assert pick.entries_cancelled is True
    assert pick.exit_reason == "take_profit"
    assert pick.legs[1].order_id is None
    assert broker.get_positions().get(pick.ticker, 0) == 0


def test_split_pending_exit_stays_managed_until_position_is_zero(env, monkeypatch):
    pipeline, broker, manager, db = env
    pick = _split_pick(db)
    _run(pipeline, broker, manager, db, [pick], {pick.ticker: 63_000})
    original_place = broker.place_order

    def pending_exit(order):
        if order.side == OrderSide.SELL:
            return OrderResult(
                order_id="pending-exit-1",
                strategy_id=order.strategy_id,
                ticker=order.ticker,
                side=order.side,
                status=OrderStatus.PENDING,
            )
        return original_place(order)

    monkeypatch.setattr(broker, "place_order", pending_exit)
    submitted, closed = _run(
        pipeline, broker, manager, db, [pick], {pick.ticker: 81_000}
    )

    assert (submitted, closed) == (0, 0)
    assert pick.state == "BOUGHT"
    assert pick.exit_pending_reason == "take_profit"
    assert pick.exit_order_id == "pending-exit-1"

    exit_log = db.query(OrderLog).filter(OrderLog.order_id == "pending-exit-1").one()
    exit_log.status = "filled"
    exit_log.fill_qty = exit_log.qty
    broker._positions[pick.ticker].reduce(exit_log.qty)
    db.commit()

    _submitted, closed = _run(
        pipeline, broker, manager, db, [pick], {pick.ticker: 81_000}
    )
    assert closed == 1
    assert pick.state == "CLOSED"
    assert pick.exit_pending_reason is None


def test_stale_split_with_live_order_is_cancelled_before_deactivation(env):
    pipeline, broker, manager, db = env
    pick = _split_pick(db)
    pick.ref_date = trading_days_ago(dt.date.today(), 30)
    _seed_leg_order(db, pick.legs[0], status="pending", fill_qty=0)
    broker._pending["leg-order-1"] = Order(
        strategy_id=f"strategy_trade:{pick.id}:leg:1:try:1",
        ticker=pick.ticker,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=pick.legs[0].planned_qty,
        limit_price=pick.legs[0].entry_price,
    )
    db.commit()

    active = pipeline._active_strategy_trade_picks(db)
    assert pick in active
    _run(pipeline, broker, manager, db, active, {pick.ticker: 69_000})

    assert broker.get_open_orders() == []
    assert pick.entries_cancelled is True
    assert pick.state == "WATCH"
    assert pick.strategy_trade_enabled is False


def test_split_recovers_unattached_live_order_without_duplicate_submit(env):
    pipeline, broker, manager, db = env
    pick = _split_pick(db)
    order_id = "unattached-live-1"
    db.add(
        OrderLog(
            order_id=order_id,
            strategy_id=f"strategy_trade:{pick.id}:leg:1:try:1",
            ticker=pick.ticker,
            side="buy",
            qty=pick.legs[0].planned_qty,
            order_price=pick.legs[0].entry_price,
            fill_qty=0,
            status="pending",
        )
    )
    db.commit()

    submitted, _closed = _run(
        pipeline, broker, manager, db, [pick], {pick.ticker: 63_000}
    )

    assert submitted == 0
    assert pick.legs[0].order_id == order_id
    assert db.query(OrderLog).filter(OrderLog.ticker == pick.ticker, OrderLog.side == "buy").count() == 1


def test_split_recovers_unattached_filled_order_without_rebuy(env):
    pipeline, broker, manager, db = env
    pick = _split_pick(db)
    db.add(
        OrderLog(
            order_id="unattached-filled-1",
            strategy_id=f"strategy_trade:{pick.id}:leg:1:try:1",
            ticker=pick.ticker,
            side="buy",
            qty=pick.legs[0].planned_qty,
            order_price=pick.legs[0].entry_price,
            fill_price=pick.legs[0].entry_price,
            fill_qty=pick.legs[0].planned_qty,
            status="filled",
        )
    )
    db.commit()

    submitted, _closed = _run(
        pipeline, broker, manager, db, [pick], {pick.ticker: 69_000}
    )

    assert submitted == 0
    assert pick.legs[0].filled_qty == pick.legs[0].planned_qty
    assert pick.legs[0].status == "FILLED"
    assert db.query(OrderLog).filter(OrderLog.ticker == pick.ticker, OrderLog.side == "buy").count() == 1


def test_split_recovery_does_not_double_count_live_retry_partial_fill(env):
    pipeline, broker, manager, db = env
    pick = _split_pick(db)
    first = pick.legs[0]
    first.filled_qty = 4
    first.order_id = "retry-partial-2"
    first.current_order_fill_qty = 0
    first.status = "PENDING"
    db.add_all([
        OrderLog(
            order_id="expired-partial-4",
            strategy_id=f"strategy_trade:{pick.id}:leg:1:try:1",
            ticker=pick.ticker,
            side="buy",
            qty=first.planned_qty,
            order_price=first.entry_price,
            fill_price=first.entry_price,
            fill_qty=4,
            status="expired",
        ),
        OrderLog(
            order_id="retry-partial-2",
            strategy_id=f"strategy_trade:{pick.id}:leg:1:try:2",
            ticker=pick.ticker,
            side="buy",
            qty=first.planned_qty - 4,
            order_price=first.entry_price,
            fill_price=first.entry_price,
            fill_qty=2,
            status="partially_filled",
        ),
    ])
    db.commit()

    _run(pipeline, broker, manager, db, [pick], {pick.ticker: 69_000})

    assert first.filled_qty == 6
    assert first.current_order_fill_qty == 2
    assert first.status == "PARTIAL"


def test_split_recovery_never_decreases_accumulated_fill(env):
    pipeline, broker, manager, db = env
    pick = _split_pick(db)
    first = pick.legs[0]
    first.filled_qty = 6
    first.order_id = "retry-partial-lower-report"
    first.current_order_fill_qty = 2
    first.status = "PARTIAL"
    db.add_all([
        OrderLog(
            order_id="expired-partial-stable-4",
            strategy_id=f"strategy_trade:{pick.id}:leg:1:try:1",
            ticker=pick.ticker,
            side="buy",
            qty=first.planned_qty,
            order_price=first.entry_price,
            fill_price=first.entry_price,
            fill_qty=4,
            status="expired",
        ),
        OrderLog(
            order_id="retry-partial-lower-report",
            strategy_id=f"strategy_trade:{pick.id}:leg:1:try:2",
            ticker=pick.ticker,
            side="buy",
            qty=first.planned_qty - 4,
            order_price=first.entry_price,
            fill_price=first.entry_price,
            fill_qty=1,
            status="partially_filled",
        ),
    ])
    db.commit()

    _run(pipeline, broker, manager, db, [pick], {pick.ticker: 69_000})

    assert first.filled_qty == 6
    assert first.current_order_fill_qty == 2
    assert first.status == "PARTIAL"

    attached = db.query(OrderLog).filter(
        OrderLog.order_id == "retry-partial-lower-report"
    ).one()
    attached.fill_qty = 3
    db.commit()

    _run(pipeline, broker, manager, db, [pick], {pick.ticker: 69_000})

    assert first.filled_qty == 7
    assert first.current_order_fill_qty == 3
    assert first.status == "PARTIAL"


def _seed_unattached_filled_exit(db, pick, *, qty=9):
    pick.state = "BOUGHT"
    pick.entries_cancelled = True
    pick.exit_pending_reason = "stop_loss"
    db.add(OrderLog(
        order_id="unattached-exit-filled-1",
        strategy_id=f"strategy_trade:{pick.id}:exit:try:1",
        ticker=pick.ticker,
        side="sell",
        qty=qty,
        fill_price=59_000,
        fill_qty=qty,
        status="filled",
        exit_reason="stop_loss",
    ))
    db.commit()


def test_split_recovers_unattached_filled_exit_and_closes_at_zero(env):
    pipeline, broker, manager, db = env
    pick = _split_pick(db)
    _seed_unattached_filled_exit(db, pick)

    submitted, closed = _run(
        pipeline, broker, manager, db, [pick], {pick.ticker: 59_000}
    )

    assert (submitted, closed) == (0, 1)
    assert pick.state == "CLOSED"
    assert pick.exit_order_id == "unattached-exit-filled-1"
    assert db.query(OrderLog).filter(OrderLog.side == "sell").count() == 1


def test_split_recovered_filled_exit_waits_for_lagging_position(env):
    pipeline, broker, manager, db = env
    pick = _split_pick(db)
    broker.place_order(Order(
        strategy_id="seed-held-position",
        ticker=pick.ticker,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=9,
        limit_price=70_000,
    ))
    _seed_unattached_filled_exit(db, pick)

    submitted, closed = _run(
        pipeline, broker, manager, db, [pick], {pick.ticker: 59_000}
    )

    assert (submitted, closed) == (0, 0)
    assert pick.state == "BOUGHT"
    assert pick.exit_order_id == "unattached-exit-filled-1"
    assert db.query(OrderLog).filter(OrderLog.side == "sell").count() == 1

    _submitted, closed = _run(
        pipeline, broker, manager, db, [pick], {pick.ticker: 59_000}
    )
    assert closed == 0
    assert pick.state == "BOUGHT"
    assert db.query(OrderLog).filter(OrderLog.side == "sell").count() == 1

    broker._positions[pick.ticker].reduce(9)
    _submitted, closed = _run(
        pipeline, broker, manager, db, [pick], {pick.ticker: 59_000}
    )
    assert closed == 1
    assert pick.state == "CLOSED"


def test_split_exit_uses_fill_that_arrives_during_entry_cancel(env, monkeypatch):
    pipeline, broker, manager, db = env
    pick = _split_pick(db)
    _seed_leg_order(db, pick.legs[0], status="partially_filled", fill_qty=4)
    original_place = broker.place_order
    original_place(
        Order(
            strategy_id="seed-position",
            ticker=pick.ticker,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=4,
            limit_price=pick.legs[0].entry_price,
        )
    )
    broker._pending["leg-order-1"] = Order(
        strategy_id=f"strategy_trade:{pick.id}:leg:1:try:1",
        ticker=pick.ticker,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=pick.legs[0].planned_qty - 4,
        limit_price=pick.legs[0].entry_price,
    )

    def fill_then_cancel(order_id):
        row = db.query(OrderLog).filter(OrderLog.order_id == order_id).one()
        row.fill_qty = 6
        row.fill_price = pick.legs[0].entry_price
        row.status = "cancelled"
        broker._positions[pick.ticker].add(2, pick.legs[0].entry_price)
        broker._pending.pop(order_id, None)
        db.commit()
        return True

    monkeypatch.setattr(broker, "cancel_order", fill_then_cancel)
    _submitted, closed = _run(
        pipeline, broker, manager, db, [pick], {pick.ticker: 59_000}
    )

    exit_log = db.query(OrderLog).filter(OrderLog.side == "sell").one()
    assert closed == 1
    assert pick.legs[0].filled_qty == 6
    assert exit_log.qty == 6
    assert broker.get_positions().get(pick.ticker, 0) == 0


def test_stale_split_bought_pick_still_exits_without_more_entries(env):
    pipeline, broker, manager, db = env
    pick = _split_pick(db)
    _run(pipeline, broker, manager, db, [pick], {pick.ticker: 63_000})
    _run(pipeline, broker, manager, db, [pick], {pick.ticker: 69_000})
    assert pick.state == "BOUGHT"
    pick.ref_date = trading_days_ago(dt.date.today(), 30)
    db.commit()

    submitted, closed = _run(
        pipeline, broker, manager, db, [pick], {pick.ticker: 59_000}
    )

    assert (submitted, closed) == (0, 1)
    assert pick.state == "CLOSED"
    assert pick.exit_reason == "stop_loss"


def test_entry_when_price_at_or_below_buy(env):
    pipeline, broker, manager, db = env
    pick = _pick(db)
    submitted, closed = _run(pipeline, broker, manager, db, [pick], {"005930": 69000})
    assert (submitted, closed) == (1, 0)
    assert pick.entry_order_id is not None
    assert pick.state == "ARMED"            # 체결 확인은 다음 사이클
    assert broker.get_positions().get("005930") == 10


def test_no_entry_above_buy(env):
    pipeline, broker, manager, db = env
    pick = _pick(db)
    submitted, closed = _run(pipeline, broker, manager, db, [pick], {"005930": 71000})
    assert (submitted, closed) == (0, 0)
    assert pick.entry_order_id is None


def test_armed_to_bought_after_fill(env):
    pipeline, broker, manager, db = env
    pick = _pick(db)
    _run(pipeline, broker, manager, db, [pick], {"005930": 69000})   # 진입
    _run(pipeline, broker, manager, db, [pick], {"005930": 69000})   # 정산
    assert pick.state == "BOUGHT"


def test_take_profit(env):
    pipeline, broker, manager, db = env
    pick = _pick(db)
    _run(pipeline, broker, manager, db, [pick], {"005930": 69000})   # 진입
    _run(pipeline, broker, manager, db, [pick], {"005930": 69000})   # BOUGHT
    submitted, closed = _run(pipeline, broker, manager, db, [pick], {"005930": 81000})  # ≥목표
    assert (submitted, closed) == (0, 1)
    assert pick.state == "CLOSED"
    assert pick.exit_order_id is not None
    assert pick.exit_reason == "take_profit"
    assert broker.get_positions().get("005930", 0) == 0


def test_stop_loss(env):
    pipeline, broker, manager, db = env
    pick = _pick(db)
    _run(pipeline, broker, manager, db, [pick], {"005930": 69000})
    _run(pipeline, broker, manager, db, [pick], {"005930": 69000})
    submitted, closed = _run(pipeline, broker, manager, db, [pick], {"005930": 65000})  # ≤손절
    assert (submitted, closed) == (0, 1)
    assert pick.state == "CLOSED"
    assert pick.exit_reason == "stop_loss"


def test_oco_single_close(env):
    pipeline, broker, manager, db = env
    pick = _pick(db)
    _run(pipeline, broker, manager, db, [pick], {"005930": 69000})
    _run(pipeline, broker, manager, db, [pick], {"005930": 69000})
    _run(pipeline, broker, manager, db, [pick], {"005930": 81000})   # 익절로 CLOSED
    # 이미 CLOSED인 픽은 추가 사이클에서 아무 동작 없음 (이중 청산 방지)
    submitted, closed = _run(pipeline, broker, manager, db, [pick], {"005930": 65000})
    assert (submitted, closed) == (0, 0)
    assert pick.state == "CLOSED"


def test_qty_from_account_risk_when_unspecified(env):
    pipeline, broker, manager, db = env
    pick = _pick(db, qty=None, buy=70000, stop=66000)
    # risk 1% of 1억 = 1,000,000 / 4000 = 250주이나, 단일종목 노출 한도 10%로 상한:
    # 0.10 * 1억 // 70000 = 142주
    assert pipeline._strategy_trade_qty(broker, pick) == 142


def test_risk_sized_entry_respects_exposure_cap(env):
    # qty=None + 타이트 손절이어도 노출 한도 내 수량으로 진입(ExposureCapError 거부 없음)
    pipeline, broker, manager, db = env
    pick = _pick(db, qty=None, buy=70000, target=80000, stop=68000)
    submitted, closed = _run(pipeline, broker, manager, db, [pick], {"005930": 69000})
    assert submitted == 1
    qty = broker.get_positions()["005930"]
    assert qty * 70000 <= 0.10 * 100_000_000   # notional ≤ 단일종목 노출 한도


def test_qty_prefers_explicit(env):
    pipeline, broker, manager, db = env
    pick = _pick(db, qty=33)
    assert pipeline._strategy_trade_qty(broker, pick) == 33


def test_rearm_after_dead_entry_order(env):
    # entry_order_id가 있지만 그 주문이 expired/cancelled이고 포지션이 없으면 재진입 허용
    from maps.common.models import OrderLog
    pipeline, broker, manager, db = env
    pick = _pick(db)
    pick.entry_order_id = "dead-1"
    db.add(OrderLog(order_id="dead-1", strategy_id="strategy_trade", ticker="005930",
                    side="buy", qty=10, status="expired"))
    db.commit()
    submitted, closed = _run(pipeline, broker, manager, db, [pick], {"005930": 69000})
    assert submitted == 1
    assert pick.entry_order_id not in (None, "dead-1")   # 죽은 주문 정리 후 새 진입
    assert broker.get_positions().get("005930") == 10


def test_no_rearm_while_entry_order_pending(env):
    # entry_order_id가 살아있는(pending) 동안에는 재진입하지 않는다 (중복 방지)
    from maps.common.models import OrderLog
    pipeline, broker, manager, db = env
    pick = _pick(db)
    pick.entry_order_id = "pending-1"
    db.add(OrderLog(order_id="pending-1", strategy_id="strategy_trade", ticker="005930",
                    side="buy", qty=10, status="pending"))
    db.commit()
    submitted, closed = _run(pipeline, broker, manager, db, [pick], {"005930": 69000})
    assert submitted == 0
    assert pick.entry_order_id == "pending-1"


def test_submit_exit_orders_excludes_bracket_tickers(env):
    pipeline, broker, manager, db = env
    broker.place_order(Order(
        strategy_id="pullback_v3", ticker="005930", side=OrderSide.BUY,
        order_type=OrderType.LIMIT, quantity=10, limit_price=70000,
    ))
    # 브래킷이 관리하는 종목은 전략 %/ATR 손절 경로에서 제외 → 매도 시도 0
    result = pipeline._submit_exit_orders(
        db=db, broker=broker, manager=manager, ref_date=_TODAY, exclude_tickers={"005930"},
    )
    assert result == (0, 0, set())


# ── 기준일 만료 가드 ────────────────────────────────────────────────────────
# 2026-07-30: 6/30 기준 픽이 "관찰"로 남아 있다가 무장되자 17초 만에 진입 주문이
# 나갔다. 진입 조건이 `현재가 <= 매수가` 라 오래된(=높은) 매수가는 즉시 발동한다.

def _stale_pick(db, *, state="ARMED", **kw):
    """만료 기준(5거래일)을 훨씬 넘긴 픽. today 상대라 어느 날 실행해도 만료다."""
    p = _pick(db, state=state, **kw)
    p.ref_date = trading_days_ago(dt.date.today(), 30)
    db.commit()
    db.refresh(p)
    return p


def test_stale_armed_pick_never_enters(env):
    pipeline, broker, manager, db = env
    pick = _stale_pick(db)
    # 현재가가 매수가 아래 = 신선한 픽이었다면 즉시 진입했을 조건
    submitted, closed = _run(pipeline, broker, manager, db, [pick], {"005930": 69000})
    assert (submitted, closed) == (0, 0)
    assert pick.entry_order_id is None
    assert broker.get_positions().get("005930", 0) == 0


def test_stale_armed_pick_excluded_from_active_query(env):
    pipeline, _broker, _manager, db = env
    stale = _stale_pick(db, ticker="005930")
    fresh = _pick(db, ticker="000660")
    active = pipeline._active_strategy_trade_picks(db)
    ids = {p.id for p in active}
    assert fresh.id in ids
    assert stale.id not in ids


def test_stale_bought_pick_still_exits_on_stop(env):
    """**비대칭 회귀 테스트.** 보유 중인 픽은 만료돼도 청산 관리에서 빼면 안 된다.

    BOUGHT 를 제외하면 실제 보유 주식이 손절·익절 없이 방치된다 — 원래 문제보다 나쁘다.
    """
    pipeline, broker, manager, db = env
    pick = _pick(db)
    _run(pipeline, broker, manager, db, [pick], {"005930": 69000})   # 진입
    _run(pipeline, broker, manager, db, [pick], {"005930": 69000})   # BOUGHT
    assert pick.state == "BOUGHT"
    pick.ref_date = trading_days_ago(dt.date.today(), 30)            # 이제 만료
    db.commit()

    assert pipeline._active_strategy_trade_picks(db)                 # 조회에서 안 빠진다
    submitted, closed = _run(pipeline, broker, manager, db, [pick], {"005930": 65000})
    assert (submitted, closed) == (0, 1)
    assert pick.state == "CLOSED"
    assert pick.exit_reason == "stop_loss"
    assert broker.get_positions().get("005930", 0) == 0


def test_stale_bought_pick_still_takes_profit(env):
    pipeline, broker, manager, db = env
    pick = _pick(db)
    _run(pipeline, broker, manager, db, [pick], {"005930": 69000})
    _run(pipeline, broker, manager, db, [pick], {"005930": 69000})
    pick.ref_date = trading_days_ago(dt.date.today(), 30)
    db.commit()
    submitted, closed = _run(pipeline, broker, manager, db, [pick], {"005930": 81000})
    assert (submitted, closed) == (0, 1)
    assert pick.exit_reason == "take_profit"
