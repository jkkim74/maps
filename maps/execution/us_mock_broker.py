"""Persistent explicit-fill USD mock broker, independent from the service ledger."""
from __future__ import annotations
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4
from maps.common.models import USMockTruth
from maps.common.settings import get_settings
from maps.common.exceptions import BrokerAdapterError, BrokerOrderRejectedError
from maps.execution.broker_adapter import Order, OrderResult, OrderStatus, OrderSide, Position, PositionSnapshot, AccountBalance, BuyingPower, CancelResult
from maps.execution.us_contracts import USQuote, us_account_key
from maps.execution.safety import account_execution_lock

class USMockBroker:
    """Orders remain pending until an explicit simulator fill; restarts preserve truth."""

    def __init__(self, db, settings=None, now=None):
        from sqlalchemy.orm import Session
        self.db = Session(bind=db.get_bind())
        self.settings = settings or get_settings()
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.key = us_account_key(self.settings)
        with account_execution_lock(self.key):
            if self.db.get(USMockTruth, self.key) is None:
                cash = self.settings.maps_us_mock_cash_usd
                if cash is None:
                    raise BrokerAdapterError('us_mock_cash_not_configured')
                self.db.add(USMockTruth(account_key=self.key, cash=cash, payload={'orders': {}, 'positions': {}, 'quotes': {}}))
                self.db.commit()

    def _truth(self):
        self.db.flush()
        self.db.expire_all()
        return self.db.get(USMockTruth, self.key)

    def set_quote(self, ticker: str, exchange: str, price: Decimal, *, as_of=None, delayed=False) -> None:
        """Persist explicit fixture quote evidence separately from orders."""
        with account_execution_lock(self.key):
            truth = self._truth()
            payload = dict(truth.payload)
            quotes = dict(payload['quotes'])
            quotes[f'{ticker}:{exchange}'] = {'price': str(price), 'as_of': (as_of or self.now()).isoformat(), 'delayed': delayed}
            payload['quotes'] = quotes
            truth.payload = payload
            self.db.commit()

    def get_quote(self, ticker: str, exchange: str) -> USQuote:
        """Read explicitly supplied quote; missing data never receives a fallback."""
        q = self._truth().payload['quotes'].get(f'{ticker}:{exchange}')
        if q is None:
            raise BrokerAdapterError('quote_missing')
        return USQuote(ticker, exchange, Decimal(q['price']), datetime.fromisoformat(q['as_of']), q['delayed'])

    def get_execution_snapshot(self) -> PositionSnapshot:
        """Read independent mock cash and shares with current mark evidence."""
        truth = self._truth()
        positions = {}
        for ticker, p in truth.payload['positions'].items():
            q = truth.payload['quotes'].get(f"{ticker}:{p['exchange']}")
            price = Decimal(q['price']) if q else None
            positions[ticker] = Position(ticker, p['quantity'], Decimal(p['cost']) / p['quantity'] if p['quantity'] else Decimal(0), current_price=price, sellable_quantity=p['quantity'])
        value = sum((p.market_value for p in positions.values()), Decimal(0))
        return PositionSnapshot(positions, AccountBalance(truth.cash, value), self.now())

    def get_buying_power(self, order: Order) -> BuyingPower:
        """Return conservative cash after broker-side pending reservations."""
        truth = self._truth()
        reserved = sum((Decimal(o['price']) * (o['quantity'] - o['filled']) for o in truth.payload['orders'].values() if o['side'] == 'buy' and o['status'] in ('pending', 'partially_filled', 'cancel_requested')), Decimal(0))
        price = Decimal(str(order.limit_price))
        available = max(Decimal(0), truth.cash - reserved)
        return BuyingPower(available, int(available / price), price, self.now())

    def place_order(self, order: Order) -> OrderResult:
        """Persist broker acceptance before returning; never auto-fill at invented price."""
        with account_execution_lock(self.key):
            truth = self._truth()
            payload = dict(truth.payload)
            orders = dict(payload['orders'])
            oid = 'usmock-' + uuid4().hex
            orders[oid] = {'ticker': order.ticker, 'exchange': order.exchange, 'strategy_id': order.strategy_id, 'side': order.side.value, 'quantity': order.quantity, 'price': str(order.limit_price), 'status': 'pending', 'filled': 0, 'gross': '0', 'commission': '0', 'tax': '0', 'created_at': self.now().isoformat()}
            payload['orders'] = orders
            truth.payload = payload
            self.db.commit()
            return self._result(oid, orders[oid])

    def _result(self, oid, o) -> OrderResult:
        status = 'pending' if o['status'] == 'cancel_requested' else o['status']
        return OrderResult(oid, o['strategy_id'], o['ticker'], OrderSide(o['side']), OrderStatus(status), filled_quantity=o['filled'], avg_price=Decimal(o['gross']) / o['filled'] if o['filled'] else Decimal(0), commission=Decimal(o['commission']), submitted_at=datetime.fromisoformat(o['created_at']), quantity=o['quantity'], order_price=Decimal(o['price']), remaining_quantity=o['quantity'] - o['filled'], cumulative_gross=Decimal(o['gross']), tax=Decimal(o['tax']), costs_complete=True, exchange=o['exchange'])

    def get_order_history(self, start, end) -> list[OrderResult]:
        """Confirm accepted cancellations on the next independent reconciliation read."""
        with account_execution_lock(self.key):
            truth = self._truth()
            payload = dict(truth.payload)
            orders = {k: dict(v) for k, v in payload['orders'].items()}
            for o in orders.values():
                if o['status'] == 'cancel_requested':
                    o['status'] = 'cancelled'
            payload['orders'] = orders
            truth.payload = payload
            self.db.commit()
            return [self._result(oid, o) for oid, o in orders.items() if start <= datetime.fromisoformat(o['created_at']).date() <= end]

    def cancel_order(self, order_id: str) -> CancelResult:
        """Cancellation acceptance is distinct from terminal confirmation."""
        with account_execution_lock(self.key):
            truth = self._truth()
            payload = dict(truth.payload)
            orders = {k: dict(v) for k, v in payload['orders'].items()}
            if order_id not in orders:
                raise BrokerAdapterError('order_not_found')
            o = orders[order_id]
            if o['status'] in ('filled', 'cancelled'):
                return CancelResult(False, o['status'] == 'cancelled')
            o['status'] = 'cancel_requested'
            payload['orders'] = orders
            truth.payload = payload
            self.db.commit()
            return CancelResult(True, False)

    def fill(self, order_id: str, quantity: int, price: Decimal, commission: Decimal=Decimal(0), tax: Decimal=Decimal(0)) -> None:
        """Inject a real simulator execution, enforcing price, inventory and cash."""
        with account_execution_lock(self.key):
            truth = self._truth()
            payload = dict(truth.payload)
            orders = {k: dict(v) for k, v in payload['orders'].items()}
            positions = {k: dict(v) for k, v in payload['positions'].items()}
            o = orders[order_id]
            if quantity <= 0 or quantity > o['quantity'] - o['filled'] or o['status'] in ('filled', 'cancelled', 'rejected'):
                raise BrokerOrderRejectedError('invalid_mock_fill')
            if o['side'] == 'buy' and price > Decimal(o['price']) or (o['side'] == 'sell' and price < Decimal(o['price'])):
                raise BrokerOrderRejectedError('limit_price_violation')
            gross = price * quantity
            cost = gross + commission + tax
            p = positions.get(o['ticker'], {'quantity': 0, 'cost': '0', 'exchange': o['exchange']})
            if o['side'] == 'buy':
                if truth.cash < cost:
                    raise BrokerOrderRejectedError('insufficient_cash')
                truth.cash -= cost
                p['quantity'] += quantity
                p['cost'] = str(Decimal(p['cost']) + cost)
            else:
                if p['quantity'] < quantity:
                    raise BrokerOrderRejectedError('insufficient_shares')
                p['cost'] = str(Decimal(p['cost']) * (p['quantity'] - quantity) / p['quantity'])
                p['quantity'] -= quantity
                truth.cash += gross - commission - tax
            positions[o['ticker']] = p
            o['filled'] += quantity
            o['gross'] = str(Decimal(o['gross']) + gross)
            o['commission'] = str(Decimal(o['commission']) + commission)
            o['tax'] = str(Decimal(o['tax']) + tax)
            o['status'] = 'filled' if o['filled'] == o['quantity'] else 'partially_filled'
            payload.update(orders=orders, positions=positions)
            truth.payload = payload
            self.db.commit()

    def is_market_open(self) -> bool:
        """Service owns exchange-calendar checks; this simulator does not override them."""
        from maps.market.us_trading_rules import is_us_market_open
        return is_us_market_open(self.now())
