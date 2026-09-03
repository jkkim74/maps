"""청산 주문의 감사 로그 가격 기록 회귀 테스트.

2026-06 운영에서 매도 13건이 `order_price` NULL 로 기록됐다. 현재가도 최근 종가도
없으면 `_log_order` 의 `limit_price or current_price or None` 이 NULL 로 떨어지고,
포지션 기반 체결 보정이 `fill_price = order_price` 로 채우므로 체결가까지 비어
매매일지 손익이 추정값이나 null 이 된다.

기록용 가격은 평균 단가로 폴백하되, **청산 판정에는 쓰지 않는다** —
폴백 가격으로 손절을 발동시키면 가짜 손절이 나간다.
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import maps.common.models  # noqa: F401 — 모델 등록
from maps.common.db import Base
from maps.common.models import OrderLog
from maps.common.settings import MapsSettings
from maps.execution.broker_adapter import (
    AccountBalance,
    BrokerAdapter,
    OrderResult,
    OrderSide,
    OrderStatus,
    Position,
)
from maps.execution.order_manager import OrderManager
from maps.ops.scheduler import OperationalPipeline
from maps.risk.manager import RiskManager

_ENTRY_PRICE = 10_000.0


class _NoQuoteBroker(BrokerAdapter):
    """보유는 있으나 시세 조회가 비어 있는 브로커 (current_price=None 이 기본)."""

    def __init__(self, current_price: float | None = None) -> None:
        self.placed: list = []
        self._current_price = current_price

    def place_order(self, order) -> OrderResult:
        self.placed.append(order)
        return OrderResult(
            order_id=f"SELL-{len(self.placed)}",
            strategy_id=order.strategy_id,
            ticker=order.ticker,
            side=order.side,
            status=OrderStatus.PENDING,
            filled_quantity=0,
            avg_price=0.0,
            submitted_at=dt.datetime.now(),
            filled_at=None,
        )

    def cancel_order(self, order_id: str) -> bool:
        return True

    def get_position(self, ticker: str) -> Position | None:
        return Position(
            ticker=ticker, quantity=10, avg_price=_ENTRY_PRICE,
            current_price=self._current_price,
        )

    def get_positions(self) -> dict[str, int]:
        return {"AAAA": 10}

    def get_account_balance(self) -> AccountBalance:
        return AccountBalance(cash=1_000_000.0, positions_value=0.0, total_assets=1_000_000.0)

    def is_market_open(self) -> bool:
        return True


def _setup(
    monkeypatch,
    *,
    exit_signal: bool,
    current_price: float | None = None,
    entry_atr: float | None = None,
    today_atr: float | None = None,
):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    settings = MapsSettings(
        maps_broker_mode="mock",
        maps_data_provider="mock",
        maps_live_trading_enabled=True,
        maps_plan_based_exits_enabled=False,
    )
    pipeline = OperationalPipeline(settings=settings, session_factory=factory)
    db = factory()
    db.add(OrderLog(
        order_id="buy-1", strategy_id="pullback_v3", ticker="AAAA",
        side=OrderSide.BUY.value, qty=10,
        order_price=_ENTRY_PRICE, fill_price=_ENTRY_PRICE, fill_qty=10,
        status="filled", broker="kis", mode="mock",
        atr14=entry_atr,
        created_at=dt.datetime(2026, 5, 4, 9, 0),
    ))
    db.commit()
    # OHLCV 없음 → _latest_close 는 0.0 을 돌려준다 (현재가·종가 모두 부재)
    monkeypatch.setattr(
        OperationalPipeline,
        "_latest_strategy_signal",
        staticmethod(lambda *a, **k: SimpleNamespace(
            entry_signal=False, exit_signal=exit_signal, close=0.0, atr14=today_atr
        )),
    )
    broker = _NoQuoteBroker(current_price=current_price)
    manager = OrderManager(broker=broker, risk=RiskManager(broker, db), db=db)
    return pipeline, db, broker, manager, engine


def test_exit_records_avg_price_when_quote_missing(monkeypatch) -> None:
    """현재가·종가가 모두 없어도 매도 로그에 가격이 남아야 한다."""
    pipeline, db, broker, manager, engine = _setup(monkeypatch, exit_signal=True)
    try:
        submitted, _skipped, _tickers = pipeline._submit_exit_orders(
            db=db, broker=broker, manager=manager, ref_date=dt.date(2026, 5, 5),
        )
        assert submitted == 1

        sell = db.query(OrderLog).filter(OrderLog.side == OrderSide.SELL.value).one()
        assert sell.order_price == _ENTRY_PRICE   # NULL 이면 손익이 추정값으로 떨어진다
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_fallback_price_does_not_trigger_a_stop(monkeypatch) -> None:
    """폴백 가격은 기록용이다 — 시세가 없다고 손절이 나가면 안 된다."""
    pipeline, db, broker, manager, engine = _setup(monkeypatch, exit_signal=False)
    try:
        submitted, _skipped, tickers = pipeline._submit_exit_orders(
            db=db, broker=broker, manager=manager, ref_date=dt.date(2026, 5, 5),
        )
        assert submitted == 0
        assert tickers == set()
        assert db.query(OrderLog).filter(OrderLog.side == OrderSide.SELL.value).count() == 0
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


# ── 진입 시점 ATR 고정 ────────────────────────────────────────────────────────
#
# pullback_v3: 고정 5%, ATR × 2.0, 손절폭 상한 10%. 진입가 10,000 기준
#   고정 손절            9,500
#   진입 ATR 300  → 손절 9,400   (ATR 이 이긴다, 상한 이내)
#   오늘 ATR 600  → 손절 8,800 → **상한 9,000 에 걸린다**
# 현재가 9,100 은 9,400 아래이므로 **진입 ATR 기준이면 손절**, 오늘 ATR 기준이면 아니다.
# (상한 도입 전에는 현재가 9,000 으로 갈렸다. 이제 9,000 은 상한과 같아 둘 다 손절이
#  되므로 판별 지점을 9,100 으로 올렸다.)

def test_exit_uses_entry_atr_not_todays(monkeypatch) -> None:
    """손절 판정은 진입 시점 ATR 로 한다 — 보유 중 ATR 이 커져도 손절선이 안 밀린다.

    2026-07-31 확인: 089860 이 진입 시 ATR 1,874(위험 0.50%)로 사이징됐는데
    청산 판정은 매일 재계산된 ATR(~2,057)을 써서 실제 위험이 0.55% 가 됐다.
    사이징은 진입 시 한 번뿐이므로 손절폭도 그 시점 값으로 고정돼야 한다.
    """
    pipeline, db, broker, manager, engine = _setup(
        monkeypatch, exit_signal=False,
        current_price=9_100.0, entry_atr=300.0, today_atr=600.0,
    )
    try:
        submitted, _skipped, tickers = pipeline._submit_exit_orders(
            db=db, broker=broker, manager=manager, ref_date=dt.date(2026, 5, 5),
        )
        assert submitted == 1          # 오늘 ATR(600)을 쓰면 0건이 된다
        assert tickers == {"AAAA"}
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_exit_falls_back_to_todays_atr_when_entry_atr_missing(monkeypatch) -> None:
    """진입 ATR 기록이 없는 옛 주문은 기존대로 오늘 ATR 로 판정한다."""
    pipeline, db, broker, manager, engine = _setup(
        monkeypatch, exit_signal=False,
        current_price=9_100.0, entry_atr=None, today_atr=600.0,
    )
    try:
        submitted, _skipped, _tickers = pipeline._submit_exit_orders(
            db=db, broker=broker, manager=manager, ref_date=dt.date(2026, 5, 5),
        )
        assert submitted == 0          # 오늘 ATR 기준 손절 9,000(상한) < 현재가 9,100
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


# ── 손절 근거 기록 ────────────────────────────────────────────────────────────


def test_exit_records_the_stop_rule_that_fired(monkeypatch) -> None:
    """손절 청산은 손절가와 그 근거를 함께 남긴다.

    2026-09-03 조사에서 손절 3건(419080·189330·475150)의 손절가를 OHLCV 와 ATR 로
    전부 역산해야 했다. `decision_context` 가 비어 있어 "이 손절선이 고정%인지
    ATR 인지 상한인지" 를 사후에 알 수 없었다.
    """
    pipeline, db, broker, manager, engine = _setup(
        monkeypatch, exit_signal=False,
        current_price=9_000.0, entry_atr=300.0,
    )
    try:
        submitted, _skipped, _tickers = pipeline._submit_exit_orders(
            db=db, broker=broker, manager=manager, ref_date=dt.date(2026, 5, 5),
        )
        assert submitted == 1

        sell = db.query(OrderLog).filter(OrderLog.side == OrderSide.SELL.value).one()
        ctx = sell.decision_context
        assert ctx is not None
        assert ctx["reason"] == "stop_loss"
        assert ctx["stop_price"] == 9_400        # pullback_v3: ATR 이 고정 5% 보다 넓다
        assert ctx["rule"] == "atr"
        assert ctx["atr14"] == 300.0
        assert ctx["entry_price"] == _ENTRY_PRICE
        assert ctx["current_price"] == 9_000.0
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()
