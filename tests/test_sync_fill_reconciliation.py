"""체결 동기화 버그 수정 회귀 테스트.

전일 제출된 pending 매도가 브로커에서 실제 체결(포지션 소멸)됐는데 만료로 오기록되던 버그를
방지한다: sync_broker_state가 만료 처리 전에 브로커 포지션과 대조해 보정해야 한다.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import maps.common.models  # noqa: F401 — 모델 등록
from maps.common.db import Base
from maps.common.models import OrderLog
from maps.execution.broker_adapter import (
    AccountBalance,
    BrokerAdapter,
    OrderResult,
    OrderSide,
    OrderStatus,
    SameDayBuy,
)
from maps.execution.order_manager import OrderManager
from maps.risk.manager import RiskManager


class _FakeBroker(BrokerAdapter):
    """포지션만 주입 가능한 최소 브로커 (주문/조회 메서드는 미구현)."""

    def __init__(self, positions: dict[str, int]) -> None:
        self._positions = positions

    def place_order(self, order):  # pragma: no cover - 미사용
        raise NotImplementedError

    def cancel_order(self, order_id: str) -> bool:
        return True

    def get_position(self, ticker: str):
        return None

    def get_account_balance(self) -> AccountBalance:
        return AccountBalance(cash=1_000_000.0, positions_value=0.0, total_assets=1_000_000.0)

    def is_market_open(self) -> bool:
        return False

    def get_positions(self) -> dict[str, int]:
        return dict(self._positions)


def _factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine)


def _add_pending_sell(db, ticker: str, *, days_ago: int) -> None:
    db.add(OrderLog(
        order_id=f"OID-{ticker}",
        strategy_id="pullback_v3",
        ticker=ticker, side="sell", qty=10,
        order_price=1000.0, fill_price=None, fill_qty=0,
        status="pending", broker="kis", mode="live",
        created_at=dt.datetime.utcnow() - dt.timedelta(days=days_ago),
    ))


def test_prior_day_pending_sell_reconciled_by_positions() -> None:
    engine, factory = _factory()
    db = factory()
    try:
        _add_pending_sell(db, "000660", days_ago=2)  # 포지션에 없음 → 실제 체결로 보정
        _add_pending_sell(db, "005930", days_ago=2)  # 포지션 유지 → 미체결 → 만료
        db.commit()

        broker = _FakeBroker(positions={"005930": 10})
        mgr = OrderManager(broker=broker, risk=RiskManager(broker, db), db=db)
        mgr.sync_broker_state()

        rows = {r.ticker: r for r in db.query(OrderLog).all()}
        assert rows["000660"].status == "filled"      # 포지션 소멸 = KIS 실제 체결
        assert rows["000660"].fill_price == 1000.0      # order_price로 보정
        assert rows["000660"].fill_qty == 10            # 제출 시 기록된 0도 주문수량으로 보정
        assert rows["005930"].status == "expired"      # 포지션 유지 = 진짜 미체결
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_same_day_buy_at_0855_kst_is_reconciled() -> None:
    """08:55 KST 매수는 UTC로 전일 23:55에 기록된다 — 조회 경계에서 빠지면 안 된다.

    naive한 date.today() 경계를 쓰면 스케줄러가 낸 매수가 통째로 범위 밖으로 밀려
    영영 pending으로 남고, mock_months 축적과 손절 진입가 조회가 함께 막힌다
    (2026-07-27 475150 실사례).
    """

    class _SameDayBuyBroker(_FakeBroker):
        def get_same_day_buys(self) -> dict[str, SameDayBuy]:
            return {"475150": SameDayBuy(ticker="475150", quantity=52, avg_price=79_500.0)}

    engine, factory = _factory()
    db = factory()
    try:
        kst_today = dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).date()
        # 오늘 08:55 KST == 어제 23:55 UTC (created_at은 UTC naive 저장)
        created_utc = dt.datetime.combine(kst_today, dt.time(8, 55)) - dt.timedelta(hours=9)
        db.add(OrderLog(
            order_id="OID-475150",
            strategy_id="donchian_v2",
            ticker="475150", side="buy", qty=52,
            order_price=81_800.0, fill_price=None, fill_qty=0,
            status="pending", broker="kis", mode="mock",
            created_at=created_utc,
        ))
        db.commit()

        broker = _SameDayBuyBroker(positions={"475150": 52})
        mgr = OrderManager(broker=broker, risk=RiskManager(broker, db), db=db)
        mgr.sync_broker_state()

        row = db.query(OrderLog).filter(OrderLog.ticker == "475150").one()
        assert row.status == "filled"
        assert row.fill_qty == 52
        assert row.fill_price == 79_500.0
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_filled_result_with_zero_quantity_is_normalized() -> None:
    """브로커가 status=filled + 체결수량 0 을 주면 주문 수량으로 채운다.

    2026-07-31 운영 확인: 004490 매도가 `filled` + `fill_qty=0` 으로 남아 있었다.
    하위 집계가 전부 `fill_qty > 0` 을 요구하므로(매매일지, mock_months) 그 체결은
    통째로 사라진다. 포지션 폴백 경로에만 있던 보정 규칙을 브로커 결과 경로에도
    같은 함수로 적용한다.
    """

    class _ZeroQtyFillBroker(_FakeBroker):
        def get_daily_order_results(self):
            return [
                OrderResult(
                    order_id="OID-004490", strategy_id="strategy_trade", ticker="004490",
                    side=OrderSide.SELL, status=OrderStatus.FILLED,
                    filled_quantity=0, avg_price=0.0,
                    submitted_at=dt.datetime.utcnow(), filled_at=None,
                ),
            ]

    engine, factory = _factory()
    db = factory()
    try:
        db.add(OrderLog(
            order_id="OID-004490", strategy_id="strategy_trade",
            ticker="004490", side="sell", qty=172,
            order_price=55_200.0, fill_price=None, fill_qty=0,
            status="pending", broker="kis", mode="mock",
            created_at=dt.datetime.utcnow(),
        ))
        db.commit()

        broker = _ZeroQtyFillBroker(positions={})
        mgr = OrderManager(broker=broker, risk=RiskManager(broker, db), db=db)
        mgr.sync_broker_state()

        row = db.query(OrderLog).filter(OrderLog.order_id == "OID-004490").one()
        assert row.status == "filled"
        assert row.fill_qty == 172        # 0 → 주문 수량으로 보정
        assert row.fill_price == 55_200.0  # 체결가 미기록 → 주문가로 보정
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_partial_fill_quantity_is_not_overwritten() -> None:
    """부분체결은 주문 수량으로 덮지 않는다 — 보유하지 않은 수량이 체결로 남는다."""

    class _PartialBroker(_FakeBroker):
        def get_daily_order_results(self):
            return [
                OrderResult(
                    order_id="OID-002350", strategy_id="strategy_trade", ticker="002350",
                    side=OrderSide.BUY, status=OrderStatus.PARTIALLY_FILLED,
                    filled_quantity=21, avg_price=6_150.0,
                    submitted_at=dt.datetime.utcnow(), filled_at=None,
                ),
            ]

    engine, factory = _factory()
    db = factory()
    try:
        db.add(OrderLog(
            order_id="OID-002350", strategy_id="strategy_trade",
            ticker="002350", side="buy", qty=1253,
            order_price=6_800.0, fill_price=None, fill_qty=0,
            status="pending", broker="kis", mode="mock",
            created_at=dt.datetime.utcnow(),
        ))
        db.commit()

        broker = _PartialBroker(positions={"002350": 21})
        mgr = OrderManager(broker=broker, risk=RiskManager(broker, db), db=db)
        mgr.sync_broker_state()

        row = db.query(OrderLog).filter(OrderLog.order_id == "OID-002350").one()
        assert row.status == "partially_filled"
        assert row.fill_qty == 21   # 1253 으로 부풀지 않는다
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_broker_position_error_does_not_crash_sync() -> None:
    """포지션 조회 실패 시에도 sync가 예외 없이 동작해야 한다 (기존 만료 동작 유지)."""

    class _ErrBroker(_FakeBroker):
        def get_positions(self):
            raise NotImplementedError

    engine, factory = _factory()
    db = factory()
    try:
        _add_pending_sell(db, "000660", days_ago=2)
        db.commit()
        broker = _ErrBroker(positions={})
        mgr = OrderManager(broker=broker, risk=RiskManager(broker, db), db=db)
        result = mgr.sync_broker_state()
        assert "updated_orders" in result
        # 포지션 truth가 없으므로 전일 pending은 기존대로 만료
        assert db.query(OrderLog).filter(OrderLog.ticker == "000660").one().status == "expired"
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()
