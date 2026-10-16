"""Common source-validated USD paper execution with durable reservations and ownership.

US Numeric tables deliberately avoid domestic float/KST reconciliation assumptions.
The shared account lock provides single-host writer ownership; multi-host execution
requires database fencing and is not supported by this service.
"""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import hashlib
import json
from uuid import uuid4
from zoneinfo import ZoneInfo
from typing import Any, Callable
from sqlalchemy import select
from sqlalchemy.orm import Session
from maps.common.settings import MapsSettings
from maps.common.settings import get_settings
from maps.common.models import USAccountState, USOrderLedger, USOwnedPosition, USExecutionEvent
from maps.common.exceptions import ExecutionBlockedError, BrokerAdapterError, BrokerOrderRejectedError
from maps.execution.broker_adapter import Order, OrderType, OrderSide
from maps.execution.safety import ExecutionContext, account_execution_lock
from maps.execution.us_contracts import us_account_key
ACTIVE = ('unknown', 'pending', 'partially_filled', 'cancel_requested')
ZERO = Decimal(0)

def money(value) -> Decimal:
    """Reject absent, nonfinite and binary-float accounting evidence."""
    if value is None or isinstance(value, float):
        raise ExecutionBlockedError('decimal_evidence_required')
    result = Decimal(value)
    if not result.is_finite():
        raise ExecutionBlockedError('invalid_money')
    return result

def aware(value: datetime) -> datetime:
    """Database timestamps are UTC naive; adapter timestamps must be explicit UTC."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

class USExecutionService:
    """One execution path for server-authenticated manual and registered producers."""

    def __init__(self, db: Session, broker: Any, settings: MapsSettings | None = None, now: Callable[[], datetime] | None = None, source_policies: dict[str, Callable] | None = None) -> None:
        self.db = db
        self.broker = broker
        self.settings = settings or get_settings()
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.key = us_account_key(self.settings)
        self.source_policies = source_policies or {}
        self.sync_running = False

    def _time(self) -> datetime:
        """Return a UTC naive timestamp for persistence."""
        return aware(self.now()).replace(tzinfo=None)

    def _state(self) -> USAccountState:
        """Load or initialize the account reconciliation gate."""
        state = self.db.get(USAccountState, self.key)
        if state is None:
            state = USAccountState(account_key=self.key, entry_stopped=False, blocked_reason='initial_sync_required')
            self.db.add(state)
            self.db.flush()
        return state

    def _rows(self) -> list[USOrderLedger]:
        """Read only this account's order projections."""
        return list(self.db.scalars(select(USOrderLedger).where(USOrderLedger.account_key == self.key).order_by(USOrderLedger.created_at.desc())))

    def _owned(self) -> list[USOwnedPosition]:
        """Read independently attributed position lots."""
        return list(self.db.scalars(select(USOwnedPosition).where(USOwnedPosition.account_key == self.key)))

    def _event(self, event_type: str, message: str, actor: str = 'system', order_id: str | None = None) -> None:
        """Append secret-free immutable audit evidence."""
        self.db.add(USExecutionEvent(account_key=self.key, order_id=order_id, created_at=self._time(), event_type=event_type, actor=str(actor), message=str(message)))

    def _fresh(self, at: datetime | None, max_age: float) -> None:
        """Require a nonfuture observation within the configured age."""
        if at is None:
            raise ExecutionBlockedError('observation_timestamp_missing')
        age = (aware(self.now()) - aware(at)).total_seconds()
        if age < 0 or age > max_age:
            raise ExecutionBlockedError('observation_stale')

    def _enabled(self) -> None:
        """Require dedicated paper execution and the global dry-run gate."""
        s = self.settings
        if not s.maps_us_enabled:
            raise ExecutionBlockedError('us_disabled')
        if s.maps_dry_run or get_settings().maps_dry_run:
            raise ExecutionBlockedError('dry_run')
        if s.maps_us_broker_mode not in ('mock', 'kis_paper'):
            raise ExecutionBlockedError('us_paper_only')
        if not s.maps_us_account_no:
            raise ExecutionBlockedError('us_account_missing')
        if s.maps_us_broker_mode == 'kis_paper':
            domestic = s.kis_account_no.replace('-', '').replace(' ', '')
            overseas = s.maps_us_account_no.replace('-', '').replace(' ', '')
            domestic = domestic + '01' if len(domestic) == 8 else domestic
            overseas = overseas + s.maps_us_account_product_code if len(overseas) == 8 else overseas
            if overseas == domestic or s.maps_us_app_key == s.kis_app_key:
                raise ExecutionBlockedError('us_account_not_dedicated')
        if self.broker is None:
            raise ExecutionBlockedError('us_broker_unconfigured')

    def _source(self, order: Order, ctx: ExecutionContext) -> None:
        """Trust authenticated server context and explicitly registered policies."""
        if not ctx.event_key or len(ctx.event_key) > 128:
            raise ExecutionBlockedError('request_id_required')
        if ctx.source == 'manual':
            if not ctx.is_admin or not ctx.actor or ctx.instance_id != 'manual' or (order.strategy_id != 'manual'):
                raise ExecutionBlockedError('manual_source_invalid')
        else:
            policy = self.source_policies.get(ctx.source)
            if policy is None or not policy(order, ctx, self.db):
                raise ExecutionBlockedError('source_not_registered')
            if not ctx.instance_id or not ctx.strategy_version or (not ctx.decision_at) or (not ctx.valid_until) or (not ctx.data_as_of):
                raise ExecutionBlockedError('strategy_evidence_missing')
            if not aware(ctx.data_as_of) <= aware(ctx.decision_at) <= aware(self.now()) <= aware(ctx.valid_until):
                raise ExecutionBlockedError('strategy_evidence_invalid')
        if order.side == OrderSide.SELL and (not ctx.sell_consent):
            raise ExecutionBlockedError('sell_consent_required')

    def _fingerprint(self, order: Order, ctx: ExecutionContext) -> str:
        """Bind an idempotency key to the complete economic request."""
        return hashlib.sha256(json.dumps([order.strategy_id, order.ticker, order.exchange, order.side.value, order.order_type.value, order.quantity, str(order.limit_price), order.market, order.currency, ctx.source, ctx.instance_id, ctx.strategy_version, ctx.parameters], sort_keys=True).encode()).hexdigest()

    def _validate(self, order: Order, ctx: ExecutionContext) -> Decimal:
        """Validate current evidence before any cash or share reservation."""
        self._enabled()
        self._source(order, ctx)
        if order.market != 'US' or order.currency != 'USD' or order.order_type != OrderType.LIMIT:
            raise ExecutionBlockedError('us_limit_usd_only')
        if isinstance(order.quantity, bool) or not isinstance(order.quantity, int) or order.quantity <= 0:
            raise ExecutionBlockedError('whole_shares_required')
        price = money(order.limit_price)
        if price < 1 or price != price.quantize(Decimal('.01')):
            raise ExecutionBlockedError('us_price_tick_invalid')
        allowed = {s.strip() for s in self.settings.maps_us_allowed_symbols.split(',') if s.strip()}
        if f'{order.ticker}:{order.exchange}' not in allowed:
            raise ExecutionBlockedError('symbol_not_allowed')
        from maps.market.us_trading_rules import is_us_market_open
        if not is_us_market_open(aware(self.now())):
            raise ExecutionBlockedError('us_market_closed')
        state = self._state()
        if state.last_sync_at is None or state.blocked_reason:
            raise ExecutionBlockedError(state.blocked_reason or 'initial_sync_required')
        self._fresh(state.last_sync_at, self.settings.maps_us_snapshot_max_age_seconds)
        quote = self.broker.get_quote(order.ticker, order.exchange)
        self._fresh(quote.as_of, self.settings.maps_us_quote_max_age_seconds)
        if quote.delayed is not False or money(quote.price) <= 0:
            raise ExecutionBlockedError('quote_realtime_unconfirmed')
        snapshot = self.broker.get_execution_snapshot()
        self._fresh(snapshot.as_of, self.settings.maps_us_snapshot_max_age_seconds)
        rows = self._rows()
        self._check_fresh_account(snapshot, rows)
        if order.side == OrderSide.BUY and any((r.status == 'unknown' for r in rows)):
            raise ExecutionBlockedError('unknown_order_unresolved')
        if order.side == OrderSide.BUY and any((r.filled_quantity and (not r.costs_confirmed) for r in rows)):
            raise ExecutionBlockedError('costs_unconfirmed')
        amount = price * order.quantity
        reserve_amount = amount * (1 + self.settings.maps_us_fee_buffer_rate)
        if order.side == OrderSide.BUY:
            if state.entry_stopped:
                raise ExecutionBlockedError('entry_stop')
            if state.loss_stopped:
                raise ExecutionBlockedError('daily_loss_limit')
            s = self.settings
            if any((v is None for v in (s.maps_us_max_order_usd, s.maps_us_max_exposure_usd, s.maps_us_max_daily_loss_usd))):
                raise ExecutionBlockedError('usd_limits_missing')
            if amount > s.maps_us_max_order_usd:
                raise ExecutionBlockedError('order_limit')
            reserved = sum((r.reserved_usd for r in rows), ZERO)
            cash = money(snapshot.balance.cash)
            bp = self.broker.get_buying_power(order)
            self._fresh(bp.as_of, s.maps_us_snapshot_max_age_seconds)
            if reserve_amount + reserved > cash or reserve_amount > money(bp.amount) or order.quantity > bp.quantity or (price > money(bp.price_bound)):
                raise ExecutionBlockedError('buying_power_insufficient')
            position_value = ZERO
            exchanges = dict(symbol.split(':', 1) for symbol in allowed if ':' in symbol)
            for ticker, position in snapshot.positions.items():
                if position.quantity <= 0:
                    continue
                if ticker not in exchanges:
                    raise ExecutionBlockedError('position_quote_unavailable')
                mark = self.broker.get_quote(ticker, exchanges[ticker])
                self._fresh(mark.as_of, s.maps_us_quote_max_age_seconds)
                if mark.delayed is not False or money(mark.price) <= 0:
                    raise ExecutionBlockedError('position_quote_unconfirmed')
                position_value += money(mark.price) * position.quantity
            if reserve_amount + reserved + position_value > s.maps_us_max_exposure_usd:
                raise ExecutionBlockedError('exposure_limit')
            equity = cash + position_value
            if state.session_equity is None or state.session_date != aware(self.now()).astimezone(ZoneInfo('America/New_York')).date().isoformat():
                raise ExecutionBlockedError('loss_baseline_missing')
            if state.session_equity - equity >= s.maps_us_max_daily_loss_usd:
                raise ExecutionBlockedError('daily_loss_limit')
        else:
            position = next((p for p in self._owned() if p.instance_id == ctx.instance_id and p.ticker == order.ticker and (p.exchange == order.exchange)), None)
            reserved = sum((r.reserved_quantity for r in rows if r.instance_id == ctx.instance_id and r.ticker == order.ticker and (r.exchange == order.exchange)))
            actual = snapshot.positions.get(order.ticker)
            total_reserved = sum((r.reserved_quantity for r in rows if r.ticker == order.ticker))
            sellable = actual.sellable_quantity if actual is not None else None
            if position is None or position.quantity - reserved < order.quantity or actual is None:
                raise ExecutionBlockedError('owned_shares_insufficient')
            if sellable is None or min(actual.quantity, sellable) - total_reserved < order.quantity:
                raise ExecutionBlockedError('broker_sellable_shares_insufficient')
        return amount

    def _check_fresh_account(self, snapshot, rows) -> None:
        """Fresh balances must explain changes since the last persisted reconciliation."""
        state = self._state()
        if state.observed_cash is None:
            raise ExecutionBlockedError('initial_sync_required')
        fills = {}
        flow = ZERO
        for row in rows:
            fills[row.ticker] = fills.get(row.ticker, 0) + (row.filled_quantity if row.side == 'buy' else -row.filled_quantity)
            if row.filled_quantity and not row.costs_confirmed:
                continue
            costs = (row.commission or ZERO) + (row.tax or ZERO)
            flow += -row.gross - costs if row.side == 'buy' else row.gross - costs
        if all(not row.filled_quantity or row.costs_confirmed for row in rows):
            if money(snapshot.balance.cash) - state.observed_cash != flow - state.observed_flow:
                raise ExecutionBlockedError('unexplained_cash_movement')
        actuals = {ticker: p.quantity for ticker, p in snapshot.positions.items() if p.quantity}
        for ticker in set(actuals) | set(state.observed_positions) | set(fills):
            actual_delta = actuals.get(ticker, 0) - state.observed_positions.get(ticker, 0)
            fill_delta = fills.get(ticker, 0) - state.observed_fills.get(ticker, 0)
            if actual_delta != fill_delta:
                raise ExecutionBlockedError('unexplained_position_movement')

    def preview(self, order: Order, context: ExecutionContext) -> dict:
        """Validate current evidence without submitting or reserving funds."""
        with account_execution_lock(self.key):
            try:
                amount = self._validate(order, context)
                return {'allowed': True, 'block_reasons': [], 'notional_usd': str(amount), 'fees_included': False, 'account_masked': self._mask()}
            except (ExecutionBlockedError, BrokerAdapterError) as exc:
                return {'allowed': False, 'block_reasons': [str(exc) if isinstance(exc, ExecutionBlockedError) else 'broker_unavailable'], 'notional_usd': None, 'fees_included': False, 'account_masked': self._mask()}

    def submit(self, order: Order, context: ExecutionContext) -> dict:
        """Commit UNKNOWN reservation before the only permitted broker submission."""
        with account_execution_lock(self.key):
            self._source(order, context)
            fingerprint = self._fingerprint(order, context)
            existing = next((r for r in self._rows() if r.request_id == context.event_key), None)
            if existing:
                if existing.fingerprint != fingerprint:
                    raise ExecutionBlockedError('request_id_conflict')
                return self._serialize(existing)
            try:
                amount = self._validate(order, context)
            except (ExecutionBlockedError, BrokerAdapterError) as exc:
                self._event('submission_blocked', str(exc) if isinstance(exc, ExecutionBlockedError) else 'broker_unavailable', context.actor)
                self.db.commit()
                raise
            row = USOrderLedger(id=uuid4().hex, account_key=self.key, request_id=context.event_key, fingerprint=fingerprint, instance_id=context.instance_id, source=context.source, strategy_id=order.strategy_id, evidence={'fee_buffer_rate': str(self.settings.maps_us_fee_buffer_rate), 'strategy_version': context.strategy_version, 'parameters': context.parameters, 'actor': context.actor, 'decision_at': context.decision_at.isoformat() if context.decision_at else None, 'data_as_of': context.data_as_of.isoformat() if context.data_as_of else None, 'valid_until': context.valid_until.isoformat() if context.valid_until else None, 'sell_consent': context.sell_consent}, ticker=order.ticker, exchange=order.exchange, side=order.side.value, quantity=order.quantity, limit_price=money(order.limit_price), status='unknown', reserved_usd=amount * (1 + self.settings.maps_us_fee_buffer_rate) if order.side == OrderSide.BUY else ZERO, reserved_quantity=order.quantity if order.side == OrderSide.SELL else 0, created_at=self._time(), updated_at=self._time())
            self.db.add(row)
            self._event('submission_started', 'Reserved before broker call', context.actor, row.id)
            self.db.commit()
            try:
                result = self.broker.place_order(order)
            except BrokerOrderRejectedError:
                row = self.db.get(USOrderLedger, row.id)
                row.status = 'rejected'
                row.reserved_usd = ZERO
                row.reserved_quantity = 0
                self._event('rejected', 'Broker explicitly rejected order', context.actor, row.id)
                self.db.commit()
                return self._serialize(row)
            except Exception as exc:
                self._event('submission_unknown', type(exc).__name__, context.actor, row.id)
                self.db.commit()
                return self._serialize(self.db.get(USOrderLedger, row.id))
            row = self.db.get(USOrderLedger, row.id)
            try:
                self._apply(row, result)
            except ExecutionBlockedError as exc:
                self._state().blocked_reason = str(exc)
                self._event('reconciliation_blocked', str(exc), order_id=row.id)
            self.db.commit()
            return self._serialize(row)

    def _apply(self, row: USOrderLedger, result: Any) -> None:
        """Project an exactly identified cumulative broker observation once."""
        if not result.order_id or (row.broker_order_id and result.order_id != row.broker_order_id) or result.ticker != row.ticker or (result.side.value != row.side) or (result.quantity != row.quantity) or (money(result.order_price) != row.limit_price) or (getattr(result, 'exchange', '') != row.exchange):
            raise ExecutionBlockedError('order_identity_mismatch')
        if result.submitted_at is None or result.submitted_at.tzinfo is None:
            raise ExecutionBlockedError('order_date_missing')
        if aware(result.submitted_at).astimezone(ZoneInfo('America/New_York')).date() != aware(row.created_at).astimezone(ZoneInfo('America/New_York')).date():
            raise ExecutionBlockedError('order_date_mismatch')
        if row.status in ('filled', 'cancelled', 'rejected') and result.status.value != row.status:
            raise ExecutionBlockedError('terminal_state_regression')
        qty = result.filled_quantity
        if not isinstance(qty, int) or qty < row.filled_quantity or qty > row.quantity:
            raise ExecutionBlockedError('fill_quantity_invalid')
        status = result.status.value
        if status not in ('pending', 'partially_filled', 'filled', 'cancelled', 'rejected', 'unknown'):
            raise ExecutionBlockedError('broker_status_invalid')
        if status == 'filled' and qty != row.quantity:
            raise ExecutionBlockedError('terminal_quantity_mismatch')
        gross = money(result.cumulative_gross) if result.cumulative_gross is not None else None
        if qty and (gross is None or gross <= 0 or gross < row.gross):
            raise ExecutionBlockedError('fill_gross_missing')
        gross = gross or ZERO
        if qty and (row.side == 'buy' and gross > row.limit_price * qty or (row.side == 'sell' and gross < row.limit_price * qty)):
            raise ExecutionBlockedError('fill_price_violation')
        costs = bool(result.costs_complete and result.commission is not None and (result.tax is not None))
        commission = money(result.commission) if costs else None
        tax = money(result.tax) if costs else None
        if costs and (commission < 0 or tax < 0):
            raise ExecutionBlockedError('fill_cost_invalid')
        if row.filled_quantity and row.costs_confirmed and (not costs):
            raise ExecutionBlockedError('cost_evidence_regression')
        if costs and row.commission is not None and (commission < row.commission or tax < row.tax):
            raise ExecutionBlockedError('cost_amount_regression')
        if qty == row.filled_quantity and gross != row.gross:
            raise ExecutionBlockedError('fill_gross_changed_without_quantity')
        remaining = result.remaining_quantity
        if remaining is None or not isinstance(remaining, int) or not 0 <= remaining <= row.quantity - qty:
            raise ExecutionBlockedError('remaining_quantity_mismatch')
        if status in ('pending', 'partially_filled', 'unknown') and remaining != row.quantity - qty:
            raise ExecutionBlockedError('remaining_quantity_mismatch')
        if status == 'rejected' and qty:
            raise ExecutionBlockedError('rejected_order_has_fills')
        delta = qty - row.filled_quantity
        position = next((p for p in self._owned() if p.instance_id == row.instance_id and p.ticker == row.ticker and (p.exchange == row.exchange)), None)
        if delta:
            if position is None:
                position = USOwnedPosition(account_key=self.key, instance_id=row.instance_id, ticker=row.ticker, exchange=row.exchange, quantity=0, cost_basis=ZERO)
                self.db.add(position)
            if row.side == 'buy':
                position.quantity += delta
                position.cost_basis += gross - row.gross
            else:
                if position.quantity < delta:
                    raise ExecutionBlockedError('ownership_underflow')
                position.cost_basis = position.cost_basis * (position.quantity - delta) / position.quantity
                position.quantity -= delta
        if costs and row.side == 'buy' and (position is not None) and qty:
            fee_delta = commission + tax - (row.commission or ZERO) - (row.tax or ZERO)
            if fee_delta:
                if position.quantity < qty and delta == 0:
                    raise ExecutionBlockedError('late_cost_allocation_ambiguous')
                position.cost_basis += fee_delta
        row.broker_order_id = result.order_id
        row.filled_quantity = qty
        row.gross = gross
        row.commission = commission
        row.tax = tax
        row.costs_confirmed = costs
        row.status = 'cancel_requested' if row.status == 'cancel_requested' and status in ('pending', 'partially_filled') else status
        row.reserved_usd = row.limit_price * (row.quantity - qty) * (1 + Decimal(row.evidence.get('fee_buffer_rate', '0'))) if row.side == 'buy' and row.status in ACTIVE else ZERO
        row.reserved_quantity = row.quantity - qty if row.side == 'sell' and row.status in ACTIVE else 0
        row.updated_at = self._time()
        self._event('order_observed', f'{row.status}: {qty}/{row.quantity}', order_id=row.id)
        self.db.flush()

    def sync(self) -> dict:
        """Read all outstanding history, apply exact matches and verify owned shares."""
        with account_execution_lock(self.key):
            self.sync_running = True
            try:
                if self.broker is None:
                    raise ExecutionBlockedError('us_broker_unconfigured')
                rows = self._rows()
                start = min((r.created_at.date() for r in rows), default=aware(self.now()).date()) - timedelta(days=1)
                history = self.broker.get_order_history(start, aware(self.now()).date() + timedelta(days=1))
                ids = [r.order_id for r in history]
                if len(ids) != len(set(ids)):
                    raise ExecutionBlockedError('duplicate_broker_order_identity')
                by_id = {r.order_id: r for r in history}
                if any((r.status in ACTIVE and (not r.broker_order_id or r.broker_order_id not in by_id) for r in rows)):
                    raise ExecutionBlockedError('active_order_history_incomplete')
                for row in rows:
                    if row.broker_order_id in by_id:
                        self._apply(row, by_id[row.broker_order_id])
                snapshot = self.broker.get_execution_snapshot()
                self._fresh(snapshot.as_of, self.settings.maps_us_snapshot_max_age_seconds)
                quantities = {}
                for p in self._owned():
                    quantities[p.ticker] = quantities.get(p.ticker, 0) + p.quantity
                if any((snapshot.positions.get(t) is None or snapshot.positions[t].quantity < qty for t, qty in quantities.items() if qty)):
                    raise ExecutionBlockedError('owned_shares_exceed_broker')
                state = self._state()
                cash = money(snapshot.balance.cash)
                current_positions = {ticker: p.quantity for ticker, p in snapshot.positions.items() if p.quantity}
                fills = {r.ticker: 0 for r in rows}
                flow = ZERO
                for row in rows:
                    fills[row.ticker] += row.filled_quantity if row.side == 'buy' else -row.filled_quantity
                    if row.filled_quantity and (not row.costs_confirmed):
                        continue
                    costs = (row.commission or ZERO) + (row.tax or ZERO)
                    flow += -row.gross - costs if row.side == 'buy' else row.gross - costs
                costs_complete = all((not r.filled_quantity or r.costs_confirmed for r in rows))
                if state.observed_cash is not None and costs_complete:
                    if cash - state.observed_cash != flow - state.observed_flow:
                        raise ExecutionBlockedError('unexplained_cash_movement')
                if state.observed_cash is not None:
                    tickers = set(current_positions) | set(state.observed_positions) | set(fills)
                    for ticker in tickers:
                        actual_delta = current_positions.get(ticker, 0) - state.observed_positions.get(ticker, 0)
                        expected_delta = fills.get(ticker, 0) - state.observed_fills.get(ticker, 0)
                        if actual_delta != expected_delta:
                            raise ExecutionBlockedError('unexplained_position_movement')
                if costs_complete:
                    state.observed_cash = cash
                    state.observed_flow = flow
                state.observed_positions = current_positions
                state.observed_fills = fills
                state.snapshot = {'cash_usd': str(snapshot.balance.cash), 'market_value_usd': str(snapshot.balance.positions_value), 'as_of': aware(snapshot.as_of).isoformat(), 'positions': {ticker: {'quantity': p.quantity, 'sellable_quantity': p.sellable_quantity, 'average_price': str(p.avg_price), 'current_price': str(p.current_price) if p.current_price is not None else None} for ticker, p in snapshot.positions.items()}}
                state.last_sync_at = self._time()
                state.blocked_reason = None
                day = aware(self.now()).astimezone(ZoneInfo('America/New_York')).date().isoformat()
                if state.session_date != day:
                    state.session_date = day
                    state.session_equity = money(snapshot.balance.cash) + money(snapshot.balance.positions_value)
                    state.loss_stopped = False
                if self.settings.maps_us_max_daily_loss_usd is not None and state.session_equity - money(snapshot.balance.cash) - money(snapshot.balance.positions_value) >= self.settings.maps_us_max_daily_loss_usd:
                    state.loss_stopped = True
                self._event('sync_completed', 'Broker history and owned quantities reconciled')
                self.db.commit()
            except Exception as exc:
                self.db.rollback()
                state = self._state()
                state.blocked_reason = str(exc) if isinstance(exc, ExecutionBlockedError) else 'broker_unavailable'
                self._event('sync_failed', state.blocked_reason)
                self.db.commit()
            finally:
                self.sync_running = False
            return self.status()

    def cancel(self, order_id: str, actor: str) -> dict:
        """Persist cancellation request; retain reservations until broker confirmation."""
        with account_execution_lock(self.key):
            self._enabled()
            row = self._find(order_id)
            if row.status == 'cancel_requested':
                return self._serialize(row)
            if row.status not in ACTIVE or not row.broker_order_id:
                raise ExecutionBlockedError('order_not_cancellable')
            row.status = 'cancel_requested'
            self._event('cancel_requested', 'Cancellation requested', actor, row.id)
            self.db.commit()
            try:
                result = self.broker.cancel_order(row.broker_order_id)
                self._event('cancel_accepted' if getattr(result, 'accepted', False) else 'cancel_unconfirmed', 'Awaiting history confirmation', actor, row.id)
            except Exception as exc:
                self._event('cancel_unknown', type(exc).__name__, actor, row.id)
            self.db.commit()
            return self._serialize(row)

    def set_entry_stop(self, value: bool, actor: str) -> dict:
        """Durably change manual entry latch without cancelling orders or liquidating."""
        with account_execution_lock(self.key):
            self._state().entry_stopped = value
            self._event('entry_stop' if value else 'entry_resume', 'Manual entry latch changed', actor)
            self.db.commit()
            return self.status()

    def _mask(self) -> str:
        """Return only the account suffix for screen display."""
        return '***' + self.settings.maps_us_account_no[-4:] if self.settings.maps_us_account_no else ''

    def status(self) -> dict:
        """Return secret-free status including every persistent safety block."""
        state = self._state()
        reasons = []
        try:
            self._enabled()
        except ExecutionBlockedError as exc:
            reasons.append(str(exc))
        if state.blocked_reason:
            reasons.append(state.blocked_reason)
        if state.entry_stopped:
            reasons.append('entry_stop')
        if state.loss_stopped:
            reasons.append('daily_loss_limit')
        if state.last_sync_at is None:
            reasons.append('initial_sync_required')
        elif (aware(self.now()) - aware(state.last_sync_at)).total_seconds() > self.settings.maps_us_snapshot_max_age_seconds:
            reasons.append('sync_stale')
        if any((r.status == 'unknown' for r in self._rows())):
            reasons.append('unknown_order_unresolved')
        if any((r.filled_quantity and (not r.costs_confirmed) for r in self._rows())):
            reasons.append('costs_unconfirmed')
        if any((v is None for v in (self.settings.maps_us_max_order_usd, self.settings.maps_us_max_exposure_usd, self.settings.maps_us_max_daily_loss_usd))):
            reasons.append('usd_limits_missing')
        from maps.market.us_trading_rules import is_us_market_open
        market_open = is_us_market_open(aware(self.now()))
        if not market_open:
            reasons.append('us_market_closed')
        return {'environment': self.settings.maps_us_broker_mode, 'account_masked': self._mask(), 'market_state': 'open' if market_open else 'closed', 'trading_date': state.session_date, 'last_sync_at': aware(state.last_sync_at).isoformat() if state.last_sync_at else None, 'sync_running': self.sync_running, 'entry_stopped': state.entry_stopped, 'buy_allowed': not reasons, 'block_reasons': list(dict.fromkeys(reasons)), 'symbols': [{'ticker': x.split(':')[0], 'exchange': x.split(':')[1], 'name': x.split(':')[0]} for x in self.settings.maps_us_allowed_symbols.split(',') if ':' in x]}

    def account(self) -> dict:
        """Read the persisted reconciliation snapshot without querying the broker."""
        rows = self._rows()
        reserved = sum((r.reserved_usd for r in rows), ZERO)
        snapshot = self._state().snapshot or {}
        cash = snapshot.get('cash_usd')
        return {'cash_usd': cash, 'buying_power_usd': str(max(ZERO, money(cash) - reserved)) if cash is not None else None, 'market_value_usd': snapshot.get('market_value_usd'), 'as_of': snapshot.get('as_of'), 'reserved_usd': format(reserved, '.6f'), 'unknown_order_count': sum((r.status == 'unknown' for r in rows))}

    def quote(self, ticker: str, exchange: str) -> dict:
        """Expose timestamp quality without upgrading uncertain observations."""
        allowed = {s.strip() for s in self.settings.maps_us_allowed_symbols.split(',')}
        if f'{ticker}:{exchange}' not in allowed:
            raise ExecutionBlockedError('symbol_not_allowed')
        if self.broker is None:
            raise ExecutionBlockedError('us_broker_unconfigured')
        q = self.broker.get_quote(ticker, exchange)
        age = (aware(self.now()) - aware(q.as_of)).total_seconds() if q.as_of else None
        return {'ticker': ticker, 'exchange': exchange, 'price': str(q.price), 'observed_at': aware(q.as_of).isoformat() if q.as_of else None, 'age_seconds': age, 'sellable_quantity': next((p['sellable_quantity'] for p in self.positions() if p['ticker'] == ticker), None), 'buying_power_usd': self.account()['buying_power_usd'], 'fresh': age is not None and 0 <= age <= self.settings.maps_us_quote_max_age_seconds and (q.delayed is False), 'delayed': q.delayed}

    def positions(self) -> list[dict]:
        """Read persisted broker totals and per-instance ownership without adoption."""
        owned = self._owned()
        snapshot = self._state().snapshot or {}
        actuals = snapshot.get('positions', {})
        tickers = set(actuals) | {p.ticker for p in owned}
        rows = self._rows()
        result = []
        for ticker in sorted(tickers):
            actual = actuals.get(ticker, {})
            lots = [p for p in owned if p.ticker == ticker]
            quantity = sum((p.quantity for p in lots))
            reserved = sum((r.reserved_quantity for r in rows if r.ticker == ticker))
            broker_quantity = actual.get('quantity', 0)
            sellable = actual.get('sellable_quantity')
            cost = sum((p.cost_basis for p in lots), ZERO)
            current = actual.get('current_price')
            result.append({'ticker': ticker, 'exchange': lots[0].exchange if lots else '', 'broker_quantity': broker_quantity, 'owned_quantity': quantity, 'external_quantity': max(0, broker_quantity - quantity), 'reserved_quantity': reserved, 'sellable_quantity': max(0, min(quantity, sellable) - reserved) if sellable is not None else None, 'average_price': str(cost / quantity) if quantity else None, 'current_price': current, 'unrealized_pnl': str(money(current) * quantity - cost) if current is not None else None, 'reconciliation_state': 'confirmed' if snapshot else 'unknown', 'source': ','.join(sorted({p.instance_id for p in lots})), 'as_of': snapshot.get('as_of')})
        return result

    def _find(self, order_id: str) -> USOrderLedger:
        """Enforce account ownership while loading a persisted order."""
        row = self.db.get(USOrderLedger, order_id)
        if row is None or row.account_key != self.key:
            raise ExecutionBlockedError('order_not_found')
        return row

    def _serialize(self, row: USOrderLedger) -> dict:
        """Expose a secret-free exact-money projection for APIs."""
        return {'id': row.id, 'request_id': row.request_id, 'broker_order_id': row.broker_order_id, 'ticker': row.ticker, 'exchange': row.exchange, 'side': row.side, 'quantity': row.quantity, 'filled_quantity': row.filled_quantity, 'remaining_quantity': row.quantity - row.filled_quantity if row.status in ACTIVE else 0, 'limit_price': str(row.limit_price), 'average_fill_price': str(row.gross / row.filled_quantity) if row.filled_quantity else None, 'status': row.status, 'created_at': aware(row.created_at).isoformat(), 'source': row.source, 'instance_id': row.instance_id, 'strategy_version': row.evidence.get('strategy_version'), 'cancellable': row.status in ('pending','partially_filled') and row.broker_order_id is not None, 'costs_confirmed': row.costs_confirmed, 'gross_usd': str(row.gross), 'commission_usd': str(row.commission) if row.commission is not None else None, 'tax_usd': str(row.tax) if row.tax is not None else None}

    def orders(self, filters=None, **kwargs) -> list[dict]:
        """Filter the account's durable order projection."""
        params = {**(filters or {}), **kwargs}
        rows = self._rows()
        if params.get('status'):
            rows = [r for r in rows if r.status == params['status']]
        if params.get('ticker'):
            rows = [r for r in rows if r.ticker == params['ticker']]
        if params.get('side'):
            rows = [r for r in rows if r.side == params['side']]
        if params.get('source'):
            rows = [r for r in rows if r.source == params['source']]
        for name, lower in (('date_from', True), ('date_to', False)):
            if params.get(name):
                bound = str(params[name])
                rows = [r for r in rows if (aware(r.created_at).astimezone(ZoneInfo('America/New_York')).date().isoformat() >= bound if lower else aware(r.created_at).astimezone(ZoneInfo('America/New_York')).date().isoformat() <= bound)]
        offset = max(0, int(params.get('offset', 0)))
        limit = min(500, max(1, int(params.get('limit', 100))))
        return [self._serialize(r) for r in rows[offset:offset + limit]]

    def order_detail(self, order_id: str) -> dict:
        """Return order plus immutable audit trail."""
        row = self._serialize(self._find(order_id))
        row['events'] = self.events(order_id=order_id)
        return row

    def events(self, limit=100, offset=0, order_id=None) -> list[dict]:
        """Read account-scoped audit events with bounded pagination."""
        query = select(USExecutionEvent).where(USExecutionEvent.account_key == self.key)
        if order_id:
            query = query.where(USExecutionEvent.order_id == order_id)
        values = self.db.scalars(query.order_by(USExecutionEvent.id.desc()).offset(max(0, offset)).limit(min(500, max(1, limit))))
        return [{'created_at': aware(e.created_at).isoformat(), 'event_type': e.event_type, 'order_id': e.order_id, 'actor': e.actor, 'message': e.message} for e in values]
