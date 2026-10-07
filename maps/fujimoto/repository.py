"""Durable mode ownership; transaction/commit and account locking belong to callers."""
from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass, replace
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json
import math

from sqlalchemy import event, select
from sqlalchemy.orm import Session

from maps.common.exceptions import DataQualityError
from maps.common.models import (FujimotoConfig, FujimotoCycle, FujimotoEvidence,
                                FujimotoFill, FujimotoOrder, OrderIntent)
from maps.fujimoto.domain import CycleState, Decision, Mode
from maps.strategy.live_rules import effective_stop_price
from maps.market.trading_rules import round_down_krx_price

TERMINAL = frozenset({"FILLED", "CANCELLED", "REJECTED", "EXPIRED"})
STATUSES = TERMINAL | {"RESERVED", "SUBMITTED", "PARTIAL", "UNKNOWN", "CANCEL_REQUESTED"}


def money(value: object) -> Decimal:
    """Validate a finite nonnegative monetary amount at the trust boundary."""
    try:
        number = Decimal(str(value))
    except (ValueError, ArithmeticError) as exc:
        raise DataQualityError("invalid_money") from exc
    if not number.is_finite() or number < 0 or isinstance(value, bool):
        raise DataQualityError("invalid_money")
    return number


def json_data(value: object) -> object:
    """Copy records into canonical secret-free JSON-compatible primitive data."""
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, dict):
        return {str(k): json_data(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_data(v) for v in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def fingerprint(value: object) -> str:
    """Hash exact canonical inputs, never unstable Python object representations."""
    return hashlib.sha256(json.dumps(json_data(value), sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def state_from_json(data: dict) -> CycleState:
    """Restore the immutable state projection after a process restart."""
    data = dict(data)
    if data.get("last_buy_date"):
        data["last_buy_date"] = date.fromisoformat(data["last_buy_date"])
    return CycleState(**data)


def utc_naive(value: datetime) -> datetime:
    """Treat DB naive timestamps as UTC; normalize offset-aware input."""
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


@dataclass(frozen=True)
class FillEvent:
    """Broker cumulative observation, bound to one reserved cycle order."""
    order_id: int
    account_key: str
    intent_id: str | None
    quantity: int
    gross: Decimal | float
    fees: Decimal | float
    tax: Decimal | float
    status: str
    fill_date: date


@dataclass(frozen=True)
class FillTransition:
    """Pure accounting result shared by persistence and historical replay."""
    state: CycleState
    cost_basis: Decimal
    realized_pnl: Decimal
    cash_delta: Decimal


def apply_fill_transition(state: CycleState, cost_basis: Decimal, realized_pnl: Decimal,
                          decision: Decision, ordered_quantity: int, previous: FillEvent,
                          current: FillEvent, stop_price: float | None = None) -> FillTransition:
    """Apply cumulative deltas exactly once; confirmed terminal positive buys advance."""
    if current.status not in STATUSES or current.account_key != previous.account_key or current.intent_id != previous.intent_id or current.order_id != previous.order_id:
        raise DataQualityError("fill_identity_or_status")
    if isinstance(current.quantity, bool) or not isinstance(current.quantity, int) or not previous.quantity <= current.quantity <= ordered_quantity:
        raise DataQualityError("nonmonotone_fill_quantity")
    old = tuple(money(v) for v in (previous.gross, previous.fees, previous.tax))
    new = tuple(money(v) for v in (current.gross, current.fees, current.tax))
    if any(a < b for a, b in zip(new, old)):
        raise DataQualityError("nonmonotone_fill_cost")
    if previous.status in TERMINAL and (current.quantity != previous.quantity or current.status != previous.status):
        raise DataQualityError("terminal_fill_changed")
    if current.status == "FILLED" and current.quantity != ordered_quantity:
        raise DataQualityError("incomplete_filled_order")
    delta = current.quantity - previous.quantity
    gross, fees, tax = (a - b for a, b in zip(new, old))
    if (current.quantity == 0 and any(new)) or (delta > 0 and gross <= 0):
        raise DataQualityError("fill_without_price")
    pending = current.status not in TERMINAL
    # A historical terminal correction belongs to its old order, not a newer
    # reservation in the cycle. Only the active order changes progression.
    active_order = previous.status not in TERMINAL
    changes = {"pending_order": pending} if active_order else {}
    cash_delta = Decimal(0)
    if decision.action == "buy":
        cash_delta = -(gross + fees + tax)
        cost_basis += gross + fees + tax
        changes["quantity"] = state.quantity + delta
        if delta:
            changes["last_buy_date"] = current.fill_date
            if decision.buy_stage == 1:
                changes["first_fill_price"] = float(new[0] / current.quantity)
            if stop_price is not None:
                changes["stop_price"] = max(state.stop_price or 0, stop_price)
        elif decision.buy_stage == 1 and current.quantity and new[0] != old[0]:
            changes["first_fill_price"] = float(new[0] / current.quantity)
        if active_order and not pending and current.quantity:
            changes["buy_stage"] = decision.buy_stage
            changes["averaging_down"] = state.averaging_down or decision.averaging_down
    elif decision.action == "sell":
        if delta > state.quantity:
            raise DataQualityError("oversell")
        released = cost_basis * delta / state.quantity if state.quantity else Decimal(0)
        cost_basis -= released
        cash_delta = gross - fees - tax
        realized_pnl += cash_delta - released
        changes["quantity"] = state.quantity - delta
        if decision.sell_target_ninths:
            changes["ordinary_sold_quantity"] = state.ordinary_sold_quantity + delta
        if decision.reason == "rebound_reduction":
            changes["rebound_sold_quantity"] = state.rebound_sold_quantity + delta
            if active_order and not pending and changes["rebound_sold_quantity"] >= state.rebound_basis_quantity // 3:
                changes["rebound_reduced"] = True
    else:
        raise DataQualityError("fill_without_order_action")
    return FillTransition(replace(state, **changes), cost_basis, realized_pnl, cash_delta)


@dataclass(frozen=True)
class AccountLimits:
    """Fresh broker account totals including ALL holdings and pending reservations.

    ticker_exposure includes marked shares and buy reservations for this ticker;
    reserved_cash includes Fujimoto and other strategy orders, exactly once.
    """
    nav: float
    cash: float
    ticker_exposure: float
    reserved_cash: float
    max_ticker_fraction: float
    minimum_cash_fraction: float

    def __post_init__(self) -> None:
        for value in (self.nav, self.cash, self.ticker_exposure, self.reserved_cash):
            money(value)
        if self.nav <= 0 or not 0 < money(self.max_ticker_fraction) <= 1 or not 0 <= money(self.minimum_cash_fraction) <= 1:
            raise DataQualityError("invalid_account_limits")


@dataclass(frozen=True)
class BuyPlan:
    """Rounded quantity, nondecreasing stop and explicit waiting reason."""
    quantity: int
    stop_price: float | None
    monetary_budget: float
    reason: str = "sized"
    limit_price: int | None = None


def size_buy(mode: Mode, state: CycleState, decision: Decision, cycle_budget: float,
             mode_nav: float, mode_cash: float, mode_reserved: float, cost_basis: float,
             spent_leg: float, account: AccountLimits, *, atr14: float | None = None,
             fee_rate: float = .00015) -> BuyPlan:
    """1:2:6 monetary legs capped by remaining mode, account and cycle stop risk."""
    price = decision.price_cap
    if decision.action != "buy" or price is None or price <= 0 or state.pending_order:
        return BuyPlan(0, state.stop_price, 0, "not_buyable")
    price = round_down_krx_price(price)
    if price <= 0:
        return BuyPlan(0, state.stop_price, 0, "invalid_rounded_cap")
    for value in (cycle_budget, mode_nav, mode_cash, mode_reserved, cost_basis, spent_leg, fee_rate):
        money(value)
    if decision.buy_stage not in (1, 2, 3) or decision.buy_stage != state.buy_stage + 1 or decision.buy_weight != (1, 2, 6)[decision.buy_stage - 1]:
        raise DataQualityError("invalid_buy_leg")
    stop = None
    if mode == Mode.SAFE:
        stop = max(state.stop_price or 0, effective_stop_price(mode.strategy_id, price, atr14) or 0)
        if stop <= 0:
            return BuyPlan(0, None, 0, "missing_required_stop")
        if price <= stop:
            return BuyPlan(0, stop, 0, "price_at_or_below_protected_stop", price)
    floor = max(.325, account.minimum_cash_fraction)
    cap = .1 if mode == Mode.SAFE else .135
    allowance = min(cycle_budget * decision.buy_weight / 9 - spent_leg,
                    mode_nav * cap - cost_basis,
                    mode_cash - mode_reserved - mode_nav * floor,
                    account.cash - account.reserved_cash - account.nav * floor,
                    account.nav * account.max_ticker_fraction - account.ticker_exposure)
    quantity = max(0, math.floor(allowance / (price * (1 + fee_rate))))
    if mode == Mode.SAFE:
        existing_risk = max(0, cost_basis - state.quantity * stop)
        per_share_risk = max(0, price - stop) + price * fee_rate
        if per_share_risk > 0:
            quantity = min(quantity, max(0, math.floor((mode_nav * .005 - existing_risk) / per_share_risk)))
    return BuyPlan(quantity, stop, max(0, allowance), "sized" if quantity else "budget_or_risk_wait", price)


def _immutable(mapper: object, connection: object, target: object) -> None:
    """Evidence and configuration versions cannot be rewritten through the ORM."""
    raise DataQualityError("immutable_fujimoto_history")


for _model in (FujimotoConfig, FujimotoEvidence, FujimotoFill):
    event.listen(_model, "before_update", _immutable)
    event.listen(_model, "before_delete", _immutable)


class FujimotoRepository:
    """Session-scoped persistence; callers hold existing account lock and commit."""
    def __init__(self, session: Session) -> None:
        self.session = session

    def _get(self, model: type, identity: int) -> object:
        result = self.session.get(model, identity)
        if result is None:
            raise DataQualityError("missing_fujimoto_record")
        return result

    def configurations(self, account_key: str) -> list[FujimotoConfig]:
        """Return full immutable config history for one account."""
        self.session.flush()
        return list(self.session.scalars(select(FujimotoConfig).where(FujimotoConfig.account_key == account_key).order_by(FujimotoConfig.version)))

    def configure(self, account_key: str, owner_user_id: int | None, mode: Mode,
                  budget: float, *, deposit: float = 0, settings: dict | None = None) -> FujimotoConfig:
        """Create explicit funded config; later changes cannot hide cash transfers."""
        if not isinstance(mode, Mode) or not account_key or money(budget) <= 0:
            raise DataQualityError("invalid_mode_budget")
        previous = [c for c in self.configurations(account_key) if c.mode == mode.value]
        if any(o.status not in TERMINAL for o in self.orders(account_key)):
            raise DataQualityError("unresolved_orders_block_configuration")
        if any(c.owner_user_id != owner_user_id for c in self.configurations(account_key)):
            raise DataQualityError("configuration_owner_mismatch")
        contribution = Decimal(str(deposit))
        if not contribution.is_finite() or isinstance(deposit, bool):
            raise DataQualityError("invalid_deposit")
        if previous and money(budget) != previous[-1].budget + contribution:
            raise DataQualityError("explicit_deposit_required")
        if previous and self.cash(previous[-1].id) + contribution < 0:
            raise DataQualityError("withdrawal_exceeds_cash")
        config = FujimotoConfig(account_key=account_key, owner_user_id=owner_user_id, mode=mode.value,
                                version=len(previous) + 1, budget=money(budget),
                                deposit=contribution if previous else money(budget),
                                settings=json_data(settings or {"execution_mode": "observe"}))
        self.session.add(config)
        self.session.flush()
        return config

    def cycles(self, account_key: str, mode: Mode | None = None) -> list[FujimotoCycle]:
        """Return independently owned cycles, including closed historical cycles."""
        self.session.flush()
        query = select(FujimotoCycle).where(FujimotoCycle.account_key == account_key)
        if mode is not None:
            query = query.where(FujimotoCycle.mode == mode.value)
        return list(self.session.scalars(query.order_by(FujimotoCycle.id)))

    def orders(self, account_key: str) -> list[FujimotoOrder]:
        """Return persisted intent links and reservations for the account."""
        self.session.flush()
        return list(self.session.scalars(select(FujimotoOrder).where(FujimotoOrder.account_key == account_key).order_by(FujimotoOrder.id)))

    def create_cycle(self, config_id: int, ticker: str) -> FujimotoCycle:
        """Freeze a monetary cycle ceiling and start with zero owned shares."""
        config = self._get(FujimotoConfig, config_id)
        active = [c for c in self.cycles(config.account_key, Mode(config.mode))
                  if self.state(c.id).quantity or self.state(c.id).pending_order or not self.state(c.id).buy_stage]
        if len(active) >= 5 or any(c.ticker == ticker for c in active):
            raise DataQualityError("mode_position_limit_or_duplicate")
        if config.id != [c for c in self.configurations(config.account_key) if c.mode == config.mode][-1].id:
            raise DataQualityError("obsolete_configuration")
        cycle = FujimotoCycle(config_id=config.id, account_key=config.account_key, mode=config.mode,
                               ticker=ticker, budget=config.budget * Decimal(".1" if config.mode == "safe" else ".135"),
                               state=json_data(CycleState()), cost_basis=0, realized_pnl=0)
        self.session.add(cycle)
        self.session.flush()
        return cycle

    def state(self, cycle_id: int) -> CycleState:
        """Restore fill-derived state without querying/adopting broker holdings."""
        return state_from_json(self._get(FujimotoCycle, cycle_id).state)

    def cash(self, config_id: int) -> Decimal:
        """Cash contributions and cumulative fill cashflows across config versions."""
        config = self._get(FujimotoConfig, config_id)
        total = sum((c.deposit for c in self.configurations(config.account_key) if c.mode == config.mode and c.version <= config.version), Decimal(0))
        cycle_ids = {c.id for c in self.cycles(config.account_key, Mode(config.mode))}
        for order in self.orders(config.account_key):
            if order.cycle_id in cycle_ids:
                total += (-order.gross - order.fees - order.tax if order.decision["action"] == "buy"
                          else order.gross - order.fees - order.tax)
        return total

    def reserved_cash(self, config_id: int) -> Decimal:
        """UNKNOWN and cancellation requests keep every unfilled buy reservation."""
        config = self._get(FujimotoConfig, config_id)
        ids = {c.id for c in self.cycles(config.account_key, Mode(config.mode))}
        return sum((Decimal(o.quantity - o.filled_quantity) * o.limit_price * Decimal(str(1 + o.fee_rate))
                    for o in self.orders(config.account_key) if o.cycle_id in ids and o.status not in TERMINAL
                    and o.decision["action"] == "buy"), Decimal(0))

    def owned_quantity(self, account_key: str, ticker: str) -> int:
        """Aggregate only strategy-acquired shares across independent modes."""
        return sum(self.state(c.id).quantity for c in self.cycles(account_key) if c.ticker == ticker)

    def record_evidence(self, kind: str, ticker: str, observed_at: datetime,
                        available_at: datetime, payload: dict, *, account_key: str | None = None) -> FujimotoEvidence:
        """Append exact provenance; pending corrections remain first-class observations."""
        observed_at, available_at = utc_naive(observed_at), utc_naive(available_at)
        if available_at < observed_at:
            raise DataQualityError("evidence_backdated")
        record = FujimotoEvidence(kind=kind, ticker=ticker, account_key=account_key,
                                  observed_at=observed_at, available_at=available_at,
                                  payload=json_data(payload), fingerprint=fingerprint(payload))
        self.session.add(record)
        self.session.flush()
        return record

    def evidence_as_of(self, kind: str, ticker: str, cutoff: datetime, *, account_key: str | None = None) -> list[FujimotoEvidence]:
        """Query actual first observation AND availability, with optional account scope."""
        self.session.flush()
        cutoff = utc_naive(cutoff)
        query = select(FujimotoEvidence).where(FujimotoEvidence.kind == kind, FujimotoEvidence.ticker == ticker,
                    FujimotoEvidence.observed_at <= cutoff, FujimotoEvidence.available_at <= cutoff)
        if account_key is not None:
            query = query.where(FujimotoEvidence.account_key == account_key)
        return list(self.session.scalars(query.order_by(FujimotoEvidence.observed_at, FujimotoEvidence.id)))

    def record_decision(self, cycle_id: int, evidence_id: int, decision: Decision) -> None:
        """Persist zero-rounded ordinary targets and rebound no-ops before ordering."""
        cycle = self._get(FujimotoCycle, cycle_id)
        evidence = self._get(FujimotoEvidence, evidence_id)
        if evidence.ticker not in (cycle.ticker, "*") or evidence.account_key not in (None, cycle.account_key):
            raise DataQualityError("decision_evidence_identity")
        state = self.state(cycle_id)
        if decision.sell_target_ninths:
            state = replace(state, sell_basis_quantity=state.sell_basis_quantity or decision.sell_basis_quantity,
                            sell_target_ninths=max(state.sell_target_ninths, decision.sell_target_ninths))
        if decision.reason == "rebound_reduction" and not decision.sell_quantity:
            state = replace(state, rebound_reduced=True)
        elif decision.reason == "rebound_reduction" and not state.rebound_basis_quantity:
            state = replace(state, rebound_basis_quantity=state.quantity)
        cycle.state = json_data(state)
        self.record_evidence("decision", cycle.ticker, evidence.observed_at, evidence.available_at,
                             {"cycle_id": cycle_id, "evidence_id": evidence_id, "decision": json_data(decision)}, account_key=cycle.account_key)

    def plan_buy(self, cycle_id: int, decision: Decision, account: AccountLimits,
                 marks: dict[str, float], *, atr14: float | None = None, fee_rate: float = .00015) -> BuyPlan:
        """Size using current mode NAV and caller's combined fresh account totals."""
        cycle = self._get(FujimotoCycle, cycle_id)
        config = [c for c in self.configurations(cycle.account_key) if c.mode == cycle.mode][-1]
        nav = float(self.cash(config.id))
        for owned in self.cycles(cycle.account_key, Mode(cycle.mode)):
            qty = self.state(owned.id).quantity
            if qty:
                if owned.ticker not in marks or money(marks[owned.ticker]) <= 0:
                    return BuyPlan(0, self.state(cycle_id).stop_price, 0, "missing_mark")
                nav += qty * marks[owned.ticker]
        spent = sum(float(o.gross + o.fees + o.tax) for o in self.orders(cycle.account_key)
                    if o.cycle_id == cycle_id and o.decision["action"] == "buy" and o.decision["buy_stage"] == decision.buy_stage)
        return size_buy(Mode(cycle.mode), self.state(cycle_id), decision, float(cycle.budget), nav,
                        float(self.cash(config.id)), float(self.reserved_cash(config.id)), float(cycle.cost_basis),
                        spent, account, atr14=atr14, fee_rate=fee_rate)

    def reserve_order(self, cycle_id: int, decision: Decision, evidence_id: int, quantity: int,
                      limit_price: float, *, intent_id: str | None = None, signal_date: date | None = None,
                      stop_price: float | None = None, fee_rate: float = .00015) -> FujimotoOrder:
        """Durably reserve before broker send; service must have run account sizing."""
        cycle = self._get(FujimotoCycle, cycle_id)
        state = self.state(cycle_id)
        if state.pending_order or isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0 or money(limit_price) <= 0:
            raise DataQualityError("invalid_or_conflicting_order")
        if decision.action not in {"buy", "sell"} or (decision.action == "sell" and quantity > min(state.quantity, decision.sell_quantity)):
            raise DataQualityError("oversell_or_invalid_action")
        if decision.action == "buy":
            if decision.price_cap is None or limit_price > decision.price_cap or decision.buy_stage not in (1, 2, 3) or decision.buy_stage != state.buy_stage + 1 or decision.buy_weight != (1, 2, 6)[decision.buy_stage - 1]:
                raise DataQualityError("invalid_buy_cap_or_stage")
            if state.sell_target_ninths or (state.buy_stage and not state.quantity) or (state.last_buy_date and (signal_date is None or signal_date <= state.last_buy_date)):
                raise DataQualityError("closed_selling_or_same_day_buy")
            spent = sum(o.gross + o.fees + o.tax for o in self.orders(cycle.account_key)
                        if o.cycle_id == cycle_id and o.decision["action"] == "buy" and o.decision["buy_stage"] == decision.buy_stage)
            reservation = money(limit_price) * quantity * (1 + money(fee_rate))
            if spent + reservation > cycle.budget * decision.buy_weight / 9:
                raise DataQualityError("leg_budget_exceeded")
            if cycle.mode == "safe":
                stop_price = max(state.stop_price or 0, stop_price or effective_stop_price("fujimoto_safe_v1", limit_price, None) or 0)
        order = FujimotoOrder(cycle_id=cycle_id, evidence_id=evidence_id, account_key=cycle.account_key,
                              intent_id=intent_id, decision=json_data(decision), signal_date=signal_date,
                              quantity=quantity, limit_price=money(limit_price), stop_price=stop_price,
                              fee_rate=float(money(fee_rate)), status="RESERVED", filled_quantity=0, gross=0, fees=0, tax=0)
        if intent_id is not None:
            self._check_intent(order, intent_id)
        self.record_decision(cycle_id, evidence_id, decision)
        cycle.state = json_data(replace(self.state(cycle_id), pending_order=True))
        self.session.add(order)
        self.session.flush()
        return order

    def bind_intent(self, order_id: int, intent_id: str, broker_order_id: str | None = None) -> None:
        """Attach exactly one existing OrderManager intent, never replace identity."""
        order = self._get(FujimotoOrder, order_id)
        if not intent_id or order.intent_id not in (None, intent_id):
            raise DataQualityError("intent_rebinding")
        self._check_intent(order, intent_id)
        order.intent_id, order.broker_order_id = intent_id, broker_order_id
        self.session.flush()

    def _check_intent(self, order: FujimotoOrder, intent_id: str) -> None:
        """Both prebound reservations and later binding use one identity guard."""
        intent = self.session.get(OrderIntent, intent_id)
        cycle = self._get(FujimotoCycle, order.cycle_id)
        if intent is None or intent.account_key != order.account_key or intent.ticker != cycle.ticker or intent.strategy_id != Mode(cycle.mode).strategy_id or intent.side != order.decision["action"] or intent.quantity != order.quantity or intent.request.get("source") != "fujimoto" or intent.request.get("source_id") != cycle.id:
            raise DataQualityError("intent_cycle_identity_mismatch")

    def apply_fill(self, current: FillEvent) -> CycleState:
        """Project an account/intent-bound cumulative fill atomically and idempotently."""
        order = self._get(FujimotoOrder, current.order_id)
        cycle = self._get(FujimotoCycle, order.cycle_id)
        identity = fingerprint(current)
        self.session.flush()
        duplicate = self.session.scalar(select(FujimotoFill).where(FujimotoFill.order_id == order.id, FujimotoFill.fingerprint == identity))
        if duplicate is not None:
            return state_from_json(duplicate.resulting_state)
        previous = FillEvent(order.id, order.account_key, order.intent_id, order.filled_quantity,
                             order.gross, order.fees, order.tax, order.status, current.fill_date)
        if order.status in TERMINAL and order.decision["action"] == "buy" and (money(current.gross), money(current.fees), money(current.tax)) != (order.gross, order.fees, order.tax):
            later = [o for o in self.orders(order.account_key) if o.cycle_id == cycle.id and o.id > order.id]
            if later:
                raise DataQualityError("late_cost_correction_requires_reconstruction")
        transition = apply_fill_transition(self.state(cycle.id), cycle.cost_basis, cycle.realized_pnl,
                                          Decision(**order.decision), order.quantity, previous, current, order.stop_price)
        cycle.state, cycle.cost_basis, cycle.realized_pnl = json_data(transition.state), transition.cost_basis, transition.realized_pnl
        order.filled_quantity, order.gross, order.fees, order.tax, order.status = current.quantity, money(current.gross), money(current.fees), money(current.tax), current.status
        self.session.add(FujimotoFill(order_id=order.id, fingerprint=identity, payload=json_data(current), resulting_state=json_data(transition.state)))
        self.session.flush()
        return transition.state

    def store_replay(self, inputs: object, report: dict, *, account_key: str | None = None) -> FujimotoEvidence:
        """Store reproducible runner inputs/results, never arbitrary promotion flags."""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        return self.record_evidence("replay", "*", now, now,
                                    {"inputs": json_data(inputs), "report": json_data(report)}, account_key=account_key)
