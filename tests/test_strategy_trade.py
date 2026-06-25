"""Part B 전략매매 브래킷 실행 엔진 테스트 (_process_strategy_trades)."""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import maps.common.models  # noqa: F401
from maps.common.db import Base
from maps.common.models import AnalysisPick
from maps.execution.broker_adapter import Order, OrderSide, OrderType
from maps.execution.mock_broker import MockBroker
from maps.execution.order_manager import OrderManager
from maps.ops.scheduler import OperationalPipeline

_TODAY = dt.date(2026, 6, 25)


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
    assert broker.get_positions().get("005930", 0) == 0


def test_stop_loss(env):
    pipeline, broker, manager, db = env
    pick = _pick(db)
    _run(pipeline, broker, manager, db, [pick], {"005930": 69000})
    _run(pipeline, broker, manager, db, [pick], {"005930": 69000})
    submitted, closed = _run(pipeline, broker, manager, db, [pick], {"005930": 65000})  # ≤손절
    assert (submitted, closed) == (0, 1)
    assert pick.state == "CLOSED"


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
