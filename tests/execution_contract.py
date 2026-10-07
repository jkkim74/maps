"""Explicit prior-day account evidence for execution integration scenarios."""
import datetime as dt
from decimal import Decimal
from dataclasses import replace

from maps.common.models import AccountObservation, ExecutionAccountState
from maps.execution.safety import account_key
from maps.execution.broker_adapter import AccountActivity, BuyingPower, OrderSide, PositionSnapshot


def prime_account(manager, db):
    """Start a scenario after the initial observation day, without disabling gates."""
    manager.sync_broker_state()
    rows = db.query(AccountObservation).filter_by(account_key=account_key(manager._settings), complete=True).all()
    assert rows, "The scenario broker must implement a complete account contract"
    for row in rows:
        row.ref_date -= dt.timedelta(days=1)
        row.observed_at -= dt.timedelta(days=1)
    state = db.get(ExecutionAccountState, account_key(manager._settings))
    state.ref_date = rows[-1].ref_date
    db.commit()


class SyntheticAccountContract:
    """Zero-cost test account; subclasses still provide actual orders and holdings."""
    def get_execution_snapshot(self):
        return PositionSnapshot(self.get_position_details(), self.get_account_balance(), dt.datetime.now(dt.timezone.utc))

    def get_order_history(self, start, end):
        return self.get_daily_order_results()

    def get_account_activity(self, start, end):
        return AccountActivity(True, Decimal(0), ())

    def get_buying_power(self, order):
        bound = Decimal(str(order.limit_price or order.current_price))
        reserved = sum(Decimal(str(o.order_price or 0)) * o.remaining_quantity
                       for o in self.get_open_orders() if o.side == OrderSide.BUY)
        cash = Decimal(str(self.get_account_balance().cash)) - reserved
        return BuyingPower(cash, int(cash // bound), bound, dt.datetime.now(dt.timezone.utc))

    def get_position_details(self):
        if hasattr(self, "position"):
            return {self.position.ticker: self.position} if self.position else {}
        return {t: self.get_position(t) for t in self.get_positions()}


def record_owned_leg(db, session, leg, quantity, price):
    from maps.common.models import OrderLog
    oid = leg.broker_order_id or f"scenario:{session.id}:{leg.name}"
    leg.broker_order_id = oid
    row = db.query(OrderLog).filter_by(order_id=oid).first()
    if row is None:
        row = OrderLog(order_id=oid, account_key=account_key(), environment="mock",
            strategy_id=f"limit_up_v1:{leg.name}", ticker=session.ticker, side="buy",
            qty=quantity, fill_qty=quantity, fill_price=price, status="filled")
        db.add(row)
    db.commit()
