"""Mock 브로커 — Phase 4까지 사용하는 시뮬레이션 브로커.

실제 거래를 수행하지 않으며 주문을 즉시 체결(시장가 기준)로 처리한다.
"""

from __future__ import annotations

import datetime
import logging
import uuid
from dataclasses import dataclass, field

from maps.common.exceptions import (
    BrokerAdapterError,
    DuplicateOrderError,
    KillSwitchError,
)
from maps.execution.broker_adapter import (
    AccountBalance,
    BrokerAdapter,
    Order,
    OrderResult,
    OrderSide,
    OrderStatus,
    PendingOrder,
    Position,
)

logger = logging.getLogger(__name__)

_KST = datetime.timezone(datetime.timedelta(hours=9))
_MARKET_OPEN = datetime.time(9, 0)
_MARKET_CLOSE = datetime.time(15, 30)


@dataclass
class _PositionState:
    quantity: int = 0
    avg_price: float = 0.0

    def add(self, qty: int, price: float) -> None:
        total_cost = self.avg_price * self.quantity + price * qty
        self.quantity += qty
        self.avg_price = total_cost / self.quantity if self.quantity else 0.0

    def reduce(self, qty: int) -> None:
        self.quantity -= qty
        if self.quantity <= 0:
            self.quantity = 0
            self.avg_price = 0.0


class MockBroker(BrokerAdapter):
    """인메모리 Mock 브로커.

    주문 흐름:
      1. Kill Switch 체크
      2. 중복 주문 체크 (당일 strategy_id + ticker + side 기준)
      3. 잔액/수량 검증
      4. 즉시 체결
    """

    def __init__(
        self,
        initial_cash: float = 100_000_000,
        price_feed: dict[str, float] | None = None,
    ) -> None:
        """
        Args:
            initial_cash: 초기 현금 (기본 1억 원).
            price_feed: {ticker: 현재가} 딕셔너리. 주문 시 limit_price 없으면 참조.
        """
        self._cash = float(initial_cash)
        self._price_feed: dict[str, float] = price_feed or {}
        self._positions: dict[str, _PositionState] = {}
        self._filled: list[OrderResult] = []
        self._pending: dict[str, Order] = {}
        self._kill_switch: bool = False
        self._kill_switch_approver: str | None = None
        self._today_orders: set[tuple[str, str, str]] = set()

    # ------------------------------------------------------------------
    # BrokerAdapter 구현
    # ------------------------------------------------------------------

    def place_order(self, order: Order) -> OrderResult:
        """주문을 제출한다.

        Raises:
            KillSwitchError: Kill Switch 발동 중.
            DuplicateOrderError: 당일 동일 전략+종목+방향 중복.
            BrokerAdapterError: 가격 정보 없음.
        """
        # 1. Kill Switch
        if self._kill_switch:
            raise KillSwitchError("Kill Switch 발동 — 신규 주문 차단")

        # 2. 중복 주문
        dup_key = (order.strategy_id, order.ticker, order.side.value)
        if dup_key in self._today_orders:
            raise DuplicateOrderError(order.ticker)
        self._today_orders.add(dup_key)

        # 3. 가격 결정
        price = self._resolve_price(order)

        # 4. 잔액 / 수량 검증 & 체결
        if order.side == OrderSide.BUY:
            total_cost = price * order.quantity
            if self._cash < total_cost:
                logger.warning(
                    "잔액 부족: 필요=%.0f, 보유=%.0f", total_cost, self._cash
                )
                return self._make_result(order, OrderStatus.REJECTED, 0, price, 0.0)
            self._cash -= total_cost
            pos = self._positions.setdefault(order.ticker, _PositionState())
            pos.add(order.quantity, price)
        else:
            pos = self._positions.get(order.ticker)
            held = pos.quantity if pos else 0
            if held < order.quantity:
                logger.warning(
                    "보유 수량 부족: %s 보유=%d, 요청=%d",
                    order.ticker,
                    held,
                    order.quantity,
                )
                return self._make_result(order, OrderStatus.REJECTED, 0, price, 0.0)
            self._cash += price * order.quantity
            pos.reduce(order.quantity)

        result = self._make_result(order, OrderStatus.FILLED, order.quantity, price, 0.0)
        self._filled.append(result)
        logger.info(
            "체결: %s %s %d주 @ %.0f",
            order.side.value,
            order.ticker,
            order.quantity,
            price,
        )
        return result

    def cancel_order(self, order_id: str) -> bool:
        """미체결 주문을 취소한다. (MockBroker는 즉시 체결이라 대부분 False.)"""
        if order_id in self._pending:
            del self._pending[order_id]
            logger.info("주문 취소: %s", order_id)
            return True
        return False

    def get_position(self, ticker: str) -> Position | None:
        """종목 보유 포지션. 미보유 시 None."""
        pos = self._positions.get(ticker)
        if pos is None or pos.quantity <= 0:
            return None
        current = self._price_feed.get(ticker)
        return Position(ticker=ticker, quantity=pos.quantity, avg_price=pos.avg_price, current_price=current)

    def get_account_balance(self) -> AccountBalance:
        """계좌 잔고 반환. price_feed 에 현재가가 있으면 시가 평가, 없으면 매입가 기준."""
        positions_value = sum(
            p.quantity * (self._price_feed.get(ticker) or p.avg_price)
            for ticker, p in self._positions.items()
        )
        return AccountBalance(cash=self._cash, positions_value=positions_value)

    def is_market_open(self) -> bool:
        """평일 09:00~15:30 KST 이면 True."""
        now = datetime.datetime.now(_KST)
        if now.weekday() >= 5:
            return False
        t = now.time()
        return _MARKET_OPEN <= t <= _MARKET_CLOSE

    # ------------------------------------------------------------------
    # 하위 호환
    # ------------------------------------------------------------------

    def get_positions(self) -> dict[str, int]:
        return {t: p.quantity for t, p in self._positions.items() if p.quantity > 0}

    def get_open_orders(self) -> list[PendingOrder]:
        return [
            PendingOrder(
                order_id=order_id,
                ticker=order.ticker,
                side=order.side,
                quantity=order.quantity,
                remaining_quantity=order.quantity,
                order_price=order.limit_price,
                raw={"strategy_id": order.strategy_id},
            )
            for order_id, order in self._pending.items()
        ]

    def get_daily_order_results(self) -> list[OrderResult]:
        return list(self._filled)

    # ------------------------------------------------------------------
    # MockBroker 전용 메서드
    # ------------------------------------------------------------------

    def activate_kill_switch(self) -> None:
        """Kill Switch를 활성화한다 (신규 주문 차단)."""
        self._kill_switch = True
        logger.warning("MockBroker: Kill Switch 활성화")

    def deactivate_kill_switch(self, approved_by: str) -> None:
        """Kill Switch를 해제한다. 관리자 승인 필수."""
        self._kill_switch = False
        self._kill_switch_approver = approved_by
        logger.info("MockBroker: Kill Switch 해제 by %s", approved_by)

    def eod_cleanup(self) -> None:
        """장 마감 정리: 중복 탐지 초기화, 미체결 주문 전량 취소."""
        cancelled = list(self._pending.keys())
        self._pending.clear()
        self._today_orders.clear()
        if cancelled:
            logger.info("EOD 정리: %d건 미체결 취소", len(cancelled))

    def update_prices(self, prices: dict[str, float]) -> None:
        """현재가를 갱신한다."""
        self._price_feed.update(prices)

    def set_price(self, ticker: str, price: float) -> None:
        """단일 종목 가격을 설정한다."""
        self._price_feed[ticker] = price

    def get_current_prices(self, tickers: list[str]) -> dict[str, float]:
        """price_feed에 있는 종목의 현재가를 반환한다(미보유 포함)."""
        return {
            t: float(self._price_feed[t])
            for t in tickers
            if t in self._price_feed and self._price_feed[t] > 0
        }

    @property
    def filled_orders(self) -> list[OrderResult]:
        """체결된 주문 목록."""
        return list(self._filled)

    @property
    def kill_switch_active(self) -> bool:
        return self._kill_switch

    # ------------------------------------------------------------------
    # 내부 헬퍼
    # ------------------------------------------------------------------

    def _resolve_price(self, order: Order) -> float:
        if order.limit_price is not None and order.limit_price > 0:
            return order.limit_price
        price = self._price_feed.get(order.ticker)
        if price is None:
            raise BrokerAdapterError(
                f"가격 정보 없음: {order.ticker} "
                "(limit_price 또는 price_feed 를 설정하세요)"
            )
        if price <= 0:
            raise BrokerAdapterError(f"유효하지 않은 가격: {order.ticker}={price}")
        return price

    def _make_result(
        self,
        order: Order,
        status: OrderStatus,
        filled_qty: int,
        price: float,
        commission: float,
    ) -> OrderResult:
        now = datetime.datetime.now()
        return OrderResult(
            order_id=str(uuid.uuid4()),
            strategy_id=order.strategy_id,
            ticker=order.ticker,
            side=order.side,
            status=status,
            filled_quantity=filled_qty,
            avg_price=price if status == OrderStatus.FILLED else 0.0,
            commission=commission,
            submitted_at=now,
            filled_at=now if status == OrderStatus.FILLED else None,
        )
