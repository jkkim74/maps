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
from maps.execution.broker_adapter import AccountBalance, BrokerAdapter
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
        assert rows["005930"].status == "expired"      # 포지션 유지 = 진짜 미체결
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
