"""Test double that reproduces KIS's identity and account semantics.

``MockBroker`` returns the same string for a broker order id and an audit id, so
any bug that confuses the two passes its tests. That exact confusion has now
produced three separate P0 defects in this engine, each found by reading rather
than by a failing test.

This double keeps the two apart the way KIS does:

- ``place_order`` returns a bare ODNO (``"0000000001"``); ``OrderManager`` wraps
  it into ``kis:<hash>:<date>:<odno>`` before writing ``order_log``
- ``get_open_orders`` / ``get_daily_order_results`` report **bare ODNOs**, since
  that is what the broker knows about

It also models a *shared* account: ``seed_foreign_position`` and
``seed_foreign_order`` place holdings and working orders that belong to another
strategy, which is the only way to catch code that treats
``get_position(ticker)`` as "this session's shares".
"""

from __future__ import annotations

import datetime as dt

from maps.common.exceptions import BrokerAdapterError
from maps.execution.broker_adapter import (
    AccountBalance,
    AfterHoursQuote,
    BrokerAdapter,
    Order,
    OrderResult,
    OrderSide,
    OrderStatus,
    PendingOrder,
    Position,
)


class KISLikeBroker(BrokerAdapter):
    """Broker double with KIS-shaped order ids and a shared account."""

    def __init__(self, *, cash: float = 20_000_000.0) -> None:
        """Create an empty account with no working orders."""
        self._cash = cash
        self._sequence = 0
        self._orders: dict[str, Order] = {}
        self._open: dict[str, PendingOrder] = {}
        self._results: dict[str, OrderResult] = {}
        self._positions: dict[str, Position] = {}
        self._prices: dict[str, int] = {}
        self._after_hours_volume: dict[str, int] = {}
        self.cancelled: list[str] = []
        self.cancel_fails: set[str] = set()
        self.reject_next: BrokerAdapterError | None = None
        self.submitted: list[Order] = []

    # ---- scenario setup -------------------------------------------------

    def seed_position(self, ticker: str, quantity: int, avg_price: float) -> None:
        """Place a holding that belongs to the upper-limit engine."""
        self._positions[ticker] = Position(
            ticker=ticker,
            quantity=quantity,
            avg_price=avg_price,
            current_price=self._prices.get(ticker, int(avg_price)),
        )

    def seed_foreign_position(self, ticker: str, quantity: int, avg_price: float) -> None:
        """Add another strategy's holding to the same account and ticker.

        Code that sells ``get_position(ticker).quantity`` will liquidate this too.
        """
        held = self._positions.get(ticker)
        total = (held.quantity if held else 0) + quantity
        self._positions[ticker] = Position(
            ticker=ticker,
            quantity=total,
            avg_price=avg_price,
            current_price=self._prices.get(ticker, int(avg_price)),
        )

    def seed_foreign_order(
        self, ticker: str, *, side: OrderSide, quantity: int, order_id: str = "9999999999"
    ) -> str:
        """Add a working order the upper-limit engine did not place."""
        self._open[order_id] = PendingOrder(
            order_id=order_id,
            ticker=ticker,
            side=side,
            quantity=quantity,
            remaining_quantity=quantity,
            order_price=None,
        )
        return order_id

    def set_price(self, ticker: str, price: int) -> None:
        """Set the last traded price used by quotes and positions."""
        self._prices[ticker] = price

    def set_after_hours_volume(self, ticker: str, volume: int) -> None:
        """Set the cumulative after-hours volume reported for one ticker."""
        self._after_hours_volume[ticker] = volume

    def fill(self, raw_order_id: str, *, quantity: int, price: float) -> None:
        """Mark one submitted order filled, moving the position accordingly."""
        order = self._orders[raw_order_id]
        self._results[raw_order_id] = OrderResult(
            order_id=raw_order_id,
            strategy_id="",  # KIS does not report the strategy
            ticker=order.ticker,
            side=order.side,
            status=OrderStatus.FILLED,
            filled_quantity=quantity,
            avg_price=price,
            submitted_at=dt.datetime.now(),
            filled_at=dt.datetime.now(),
        )
        self._open.pop(raw_order_id, None)
        held = self._positions.get(order.ticker)
        current = held.quantity if held else 0
        if order.side is OrderSide.SELL and quantity > current:
            raise AssertionError(
                f"체결이 보유를 초과한다: {quantity} > {current} — 실제 브로커에서 불가능하다"
            )
        remaining = current - quantity if order.side is OrderSide.SELL else current + quantity
        if remaining <= 0:
            self._positions.pop(order.ticker, None)
        else:
            self._positions[order.ticker] = Position(
                ticker=order.ticker,
                quantity=remaining,
                avg_price=held.avg_price if held else price,
                current_price=self._prices.get(order.ticker, int(price)),
            )

    def last_raw_order_id(self) -> str:
        """Return the most recently accepted broker order id."""
        return f"{self._sequence:010d}"

    # ---- BrokerAdapter --------------------------------------------------

    def place_order(self, order: Order) -> OrderResult:
        """Accept an order and return a **bare ODNO**, as KIS does.

        Rejects a sell larger than the sellable quantity, the way KIS does
        (주문가능수량 초과). Without that the double books an oversell as a clean
        exit, and every guard that exists to prevent overselling becomes
        untestable — which is how several quantity defects shipped green.
        """
        if self.reject_next is not None:
            error, self.reject_next = self.reject_next, None
            raise error
        if order.side is OrderSide.SELL:
            held = self._positions.get(order.ticker)
            reserved = sum(
                pending.remaining_quantity
                for pending in self._open.values()
                if pending.ticker == order.ticker and pending.side is OrderSide.SELL
            )
            sellable = (held.quantity if held else 0) - reserved
            if order.quantity > sellable:
                raise BrokerAdapterError(
                    f"주문가능수량 초과: {order.quantity} > {sellable} ({order.ticker})"
                )
        self._sequence += 1
        raw_id = f"{self._sequence:010d}"
        self._orders[raw_id] = order
        self.submitted.append(order)
        self._open[raw_id] = PendingOrder(
            order_id=raw_id,
            ticker=order.ticker,
            side=order.side,
            quantity=order.quantity,
            remaining_quantity=order.quantity,
            order_price=order.limit_price,
        )
        return OrderResult(
            order_id=raw_id,
            strategy_id=order.strategy_id,
            ticker=order.ticker,
            side=order.side,
            status=OrderStatus.PENDING,
            submitted_at=dt.datetime.now(),
        )

    def cancel_order(self, order_id: str) -> bool:
        """Cancel by broker order id, normalizing an audit id if given one."""
        from maps.execution.broker_adapter import raw_broker_order_id

        raw_id = raw_broker_order_id(order_id)
        if raw_id in self.cancel_fails:
            raise BrokerAdapterError(f"cancel rejected: {raw_id}")
        if raw_id not in self._open:
            return False
        self._open.pop(raw_id)
        self.cancelled.append(raw_id)
        return True

    def get_position(self, ticker: str) -> Position | None:
        """Return the **account-wide** holding, exactly as KIS reports it."""
        return self._positions.get(ticker)

    def get_positions(self) -> dict[str, int]:
        """Return every account holding."""
        return {t: p.quantity for t, p in self._positions.items()}

    def get_account_balance(self) -> AccountBalance:
        """Return cash plus marked position value."""
        value = sum(p.quantity * (p.current_price or p.avg_price) for p in self._positions.values())
        return AccountBalance(self._cash, value, self._cash + value)

    def is_market_open(self) -> bool:
        """Keep the double inside the regular session."""
        return True

    def get_open_orders(self) -> list[PendingOrder]:
        """Return working orders keyed by **bare ODNO**."""
        return list(self._open.values())

    def get_daily_order_results(self) -> list[OrderResult]:
        """Return today's fills keyed by **bare ODNO**."""
        return list(self._results.values())

    def get_after_hours_quote(self, ticker: str) -> AfterHoursQuote:
        """Return the after-hours print and cumulative volume together."""
        return AfterHoursQuote(
            price=self._prices.get(ticker, 0),
            cumulative_volume=self._after_hours_volume.get(ticker, 0),
        )

    def get_current_prices(self, tickers: list[str]) -> dict[str, float]:
        """Return set prices for the requested tickers."""
        return {t: float(self._prices[t]) for t in tickers if t in self._prices}
