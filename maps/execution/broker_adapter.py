"""브로커 어댑터 추상 인터페이스.

Phase 5까지는 MockBroker만 사용한다.
실 증권사 어댑터는 Phase 5에서만 연결한다.
"""

from __future__ import annotations

import abc
import datetime
import hashlib
from dataclasses import dataclass, field
from enum import Enum

from maps.common.settings import get_settings


_KST = datetime.timezone(datetime.timedelta(hours=9))


def order_log_id(
    raw_order_id: str,
    *,
    broker: str,
    account_no: str,
    submitted_at: datetime.datetime,
) -> str:
    """Return the globally unique audit ID for a broker order.

    KIS reuses ODNO values across trading days.  The account fingerprint and
    KST submission date scope that raw value without persisting the account
    number itself.  Other brokers keep their existing identifiers.
    """
    if broker != "kis" or raw_order_id.startswith("kis:"):
        return raw_order_id
    submitted = submitted_at
    if submitted.tzinfo is None:
        submitted = submitted.replace(tzinfo=_KST)
    day = submitted.astimezone(_KST).date()
    account_identity = account_no.strip()
    if "-" in account_identity:
        prefix, product_code = account_identity.split("-", 1)
        account_identity = f"{prefix.strip()}-{product_code.strip()}"
    elif len(account_identity) == 8:
        account_identity = f"{account_identity}-01"
    account_key = hashlib.sha256(account_identity.encode("utf-8")).hexdigest()[:8]
    return f"kis:{account_key}:{day:%Y%m%d}:{raw_order_id}"


def raw_broker_order_id(order_id: str) -> str:
    """Return the broker-native ID from an internal audit ID."""
    if order_id.startswith("kis:"):
        return order_id.rsplit(":", 1)[-1]
    return order_id


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
    # 진입 시점 ATR(14). 사이징에 쓴 값을 그대로 order_log 에 남겨 청산·화면이
    # 재사용한다. memo 와 같은 MAPS 내부 필드이고 브로커 어댑터는 읽지 않는다.
    atr14: float | None = None


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

    def get_position_details(self) -> dict[str, Position]:
        """Return detailed positions, using legacy methods when necessary."""
        return {
            ticker: position
            for ticker, quantity in self.get_positions().items()
            if quantity > 0 and (position := self.get_position(ticker)) is not None
        }

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

    def get_current_prices(self, tickers: list[str]) -> dict[str, float]:
        """미보유 종목 포함 실시간 현재가를 조회한다(지원 브로커만).

        보유 종목 시세만 주는 잔고 조회와 달리, 임의 종목의 현재가를 반환한다.
        기본 구현은 no-op(빈 딕셔너리) — 상위에서 일봉 종가로 폴백한다.
        """
        return {}

    def update_prices(self, prices: dict[str, float]) -> None:
        """장중 현재가를 갱신한다. 실시간 API를 지원하는 브로커는 이 메서드를 오버라이드한다.

        기본 구현은 no-op. MockBroker는 내부 price_feed를 갱신한다.
        KIS/Kiwoom 어댑터는 자체 실시간 API를 사용하므로 브로커별로 구현한다.
        """


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
