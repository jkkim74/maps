"""브로커 어댑터 추상 인터페이스.

Phase 5까지는 MockBroker만 사용한다.
실 증권사 어댑터는 Phase 5에서만 연결한다.
"""

from __future__ import annotations

import abc
import datetime
from dataclasses import dataclass, field
from enum import Enum

from maps.common.settings import get_settings


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"


class OrderStatus(str, Enum):
    PENDING = "pending"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


@dataclass
class Order:
    """주문 요청."""

    strategy_id: str
    ticker: str
    side: OrderSide
    order_type: OrderType
    quantity: int
    limit_price: float | None = None
    current_price: float | None = None   # 시장가 주문 시 노출 검사용 현재가
    memo: str = ""


@dataclass
class OrderResult:
    """주문 실행 결과."""

    order_id: str
    strategy_id: str
    ticker: str
    side: OrderSide
    status: OrderStatus
    filled_quantity: int = 0
    avg_price: float = 0.0
    commission: float = 0.0
    submitted_at: datetime.datetime = field(default_factory=datetime.datetime.now)
    filled_at: datetime.datetime | None = None


@dataclass
class Position:
    """보유 포지션 정보."""

    ticker: str
    quantity: int
    avg_price: float
    name: str = ""
    current_price: float | None = None
    evaluation_value: float | None = None

    @property
    def market_value(self) -> float:
        if self.evaluation_value is not None:
            return self.evaluation_value
        return self.quantity * (
            self.current_price if self.current_price is not None else self.avg_price
        )


@dataclass
class AccountBalance:
    """계좌 잔고."""

    cash: float
    positions_value: float
    total_assets: float | None = None

    @property
    def total_value(self) -> float:
        return self.total_assets if self.total_assets is not None else self.cash + self.positions_value


@dataclass
class PendingOrder:
    """Open order returned by a live broker."""

    order_id: str
    ticker: str
    side: OrderSide
    quantity: int
    remaining_quantity: int
    order_price: float | None = None
    submitted_at: datetime.datetime | None = None
    raw: dict | None = None


@dataclass
class SameDayBuy:
    """Same-day buy quantity reported by a broker balance endpoint."""

    ticker: str
    quantity: int
    avg_price: float | None = None


class BrokerAdapter(abc.ABC):
    """브로커 추상 인터페이스.

    모든 브로커 구현체는 이 클래스를 상속해야 한다.
    Phase 5 실계좌 연결 전까지는 MockBroker만 사용한다.
    """

    @abc.abstractmethod
    def place_order(self, order: Order) -> OrderResult:
        """주문을 제출한다.

        Args:
            order: 주문 요청.

        Returns:
            OrderResult.

        Raises:
            KillSwitchError: Kill Switch 발동 중.
            DuplicateOrderError: 중복 주문.
            BrokerAdapterError: 기타 주문 오류.
        """

    @abc.abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """주문을 취소한다.

        Returns:
            취소 성공 여부.
        """

    @abc.abstractmethod
    def get_position(self, ticker: str) -> Position | None:
        """특정 종목 보유 포지션을 반환한다. 미보유 시 None."""

    @abc.abstractmethod
    def get_account_balance(self) -> AccountBalance:
        """계좌 잔고를 반환한다."""

    @abc.abstractmethod
    def is_market_open(self) -> bool:
        """장이 열려 있으면 True."""

    def subscribe_realtime(self, *args, **kwargs) -> None:
        """실시간 시세 구독 (Phase 5 전용)."""
        raise NotImplementedError("subscribe_realtime은 Phase 5에서만 사용합니다.")

    # ------------------------------------------------------------------
    # 하위 호환 메서드
    # ------------------------------------------------------------------

    def submit_order(self, order: Order) -> OrderResult:
        """place_order 의 하위 호환 별칭."""
        return self.place_order(order)

    def get_positions(self) -> dict[str, int]:
        """보유 포지션 {ticker: quantity} 딕셔너리 (하위 호환)."""
        raise NotImplementedError

    def get_balance(self) -> float:
        """현금 잔고 (하위 호환)."""
        return self.get_account_balance().cash

    def get_open_orders(self) -> list[PendingOrder]:
        """Return open/unfilled orders when the broker supports it."""
        raise NotImplementedError

    def get_daily_order_results(self) -> list[OrderResult]:
        """Return same-day broker order/fill states when supported."""
        raise NotImplementedError

    def get_same_day_buys(self) -> dict[str, SameDayBuy]:
        """Return broker-reported same-day buy quantities when supported."""
        raise NotImplementedError


def get_broker(mode: str | None = None, **kwargs) -> BrokerAdapter:
    """브로커 어댑터 팩토리.

    Args:
        mode: "mock" | "kis" | "kiwoom"
        **kwargs: MockBroker 전용 — initial_cash, price_feed

    Returns:
        BrokerAdapter 구현체.

    Raises:
        ValueError: 알 수 없는 mode.
        BrokerAdapterError: 자격증명 환경변수 누락 (kis/kiwoom).
    """
    resolved_mode = mode or get_settings().maps_broker_mode
    if resolved_mode == "mock":
        from maps.execution.mock_broker import MockBroker
        return MockBroker(**kwargs)
    if resolved_mode == "kis":
        from maps.execution.kis_adapter import KISAdapter
        return KISAdapter()
    if resolved_mode == "kiwoom":
        from maps.execution.kiwoom_adapter import KiwoomAdapter
        return KiwoomAdapter()
    raise ValueError(f"알 수 없는 브로커 모드: {mode!r}  (mock | kis | kiwoom)")
