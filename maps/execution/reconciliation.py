"""Broker evidence, monotone fills, and cash-flow-adjusted account risk."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from maps.common.exceptions import BrokerAdapterError, ExecutionBlockedError
from maps.common.models import (
    AccountAdjustment, AccountObservation, ExecutionAccountState, ExecutionSafetyEvent,
    KillSwitchLog, OrderIntent, OrderLog,
)
from maps.common.settings import get_settings
from maps.execution.broker_adapter import OrderSide, OrderStatus, raw_broker_order_id
from maps.execution.safety import account_key, execution_environment, utcnow

KST = dt.timezone(dt.timedelta(hours=9))
ACTIVE = ("PREPARED", "SENDING", "UNKNOWN", "ACKNOWLEDGED", "PARTIALLY_FILLED")
TERMINAL = ("filled", "cancelled", "rejected")


def money(value) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite():
        raise ExecutionBlockedError("nonfinite_account_value")
    return result


def event(db, key: str, reason: str, details: dict | None = None) -> None:
    db.add(ExecutionSafetyEvent(account_key=key, reason_code=reason,
        details=details or {}, created_at=utcnow()))


def audit_id(result, settings=None) -> str:
    settings = settings or get_settings()
    if settings.maps_broker_mode != "kis":
        return result.order_id
    if result.order_id.startswith("kis:"):
        if not result.order_id.startswith(f"kis:{execution_environment(settings)}:{account_key(settings)[:12]}:"):
            raise ExecutionBlockedError("broker_account_identity_mismatch")
        return result.order_id
    timestamp = result.submitted_at
    if timestamp is None:
        raise ExecutionBlockedError("order_timestamp_missing")
    day = timestamp.replace(tzinfo=KST).date() if timestamp.tzinfo is None else timestamp.astimezone(KST).date()
    return f"kis:{execution_environment(settings)}:{account_key(settings)[:12]}:{day:%Y%m%d}:{result.order_id}"


def apply_result(db, row: OrderLog, result) -> bool:
    """No quantity/price invention and no regression on delayed observations."""
    qty = result.filled_quantity
    if row.ticker != result.ticker or row.side != result.side.value:
        raise ExecutionBlockedError("broker_identity_mismatch")
    if (not isinstance(qty, int) or isinstance(qty, bool) or qty < 0 or qty > row.qty
            or (qty > 0 and money(result.avg_price) <= 0)):
        raise ExecutionBlockedError("invalid_fill_evidence")
    if result.status == OrderStatus.FILLED and qty != row.qty:
        raise ExecutionBlockedError("filled_quantity_missing")
    if qty < (row.fill_qty or 0):
        return False
    status = result.status.value
    if row.status in TERMINAL and status not in TERMINAL:
        return False
    changed = row.fill_qty != qty or row.status != status or (qty and row.fill_price != result.avg_price)
    row.fill_qty = qty
    if qty:
        row.fill_price = result.avg_price
    if row.status not in TERMINAL or status == row.status or (status == "filled" and qty == row.qty):
        row.status = status
    if row.intent_id:
        intent = db.get(OrderIntent, row.intent_id)
        if intent:
            intent.filled_quantity = qty
            intent.status = {"pending": "ACKNOWLEDGED"}.get(row.status, row.status.upper())
            remaining = intent.quantity - qty if row.status not in TERMINAL else 0
            intent.reserved_quantity = remaining if intent.side == "sell" else 0
            intent.reserved_amount = money(intent.request.get("price_bound", 0)) * remaining if intent.side == "buy" else 0
            intent.version += 1
            intent.updated_at = utcnow()
    return bool(changed)


def reconcile(db, broker, settings=None):
    settings = settings or get_settings()
    key, now = account_key(settings), utcnow()
    today = dt.datetime.now(KST).date()
    state = db.get(ExecutionAccountState, key)
    if state is None:
        state = ExecutionAccountState(account_key=key, environment=execution_environment(settings),
            status="WARMUP", block_reasons=[], version=0, killed=False)
        db.add(state)
        db.flush()
    previous_status = (state.status, state.block_reasons)
    baseline = db.query(AccountObservation).filter_by(account_key=key, complete=True).order_by(AccountObservation.id.desc()).first()
    first = db.query(AccountObservation).filter_by(account_key=key, complete=True).order_by(AccountObservation.id).first()
    intents = db.query(OrderIntent).filter(OrderIntent.account_key == key, OrderIntent.status.in_(ACTIVE)).all()
    for intent in intents:
        if intent.status == "PREPARED" and intent.valid_until <= now:
            # No broker call has started. This is not expiry of an unknown order.
            intent.status = "REJECTED"
            intent.reserved_amount = 0
            intent.reserved_quantity = 0
            intent.updated_at = now
            intent.version += 1
            event(db, key, "intent_expired_before_send", {"intent_id": intent.id})
        if intent.status == "SENDING":
            intent.status = "UNKNOWN"
            intent.updated_at = now
            intent.version += 1
    start = min([today, baseline.ref_date if baseline else today] + [(i.created_at + dt.timedelta(hours=9)).date() for i in intents])
    reasons, updated = [], 0
    snapshot, open_orders, results = None, [], []
    try:
        results = broker.get_order_history(start, today)
        open_orders = broker.get_open_orders()
        snapshot = broker.get_execution_snapshot()
        age = (dt.datetime.now(dt.timezone.utc) - snapshot.as_of.astimezone(dt.timezone.utc)).total_seconds()
        if age < -5 or age > settings.maps_execution_snapshot_max_age_seconds:
            raise ExecutionBlockedError("account_snapshot_stale")
    except (BrokerAdapterError, NotImplementedError, ValueError) as exc:
        snapshot = None
        reasons.append(getattr(exc, "reason_code", "broker_query_incomplete"))
    seen = set()
    orders = {}
    aliases = {}
    for resolution in db.query(ExecutionSafetyEvent).filter_by(
            account_key=key, reason_code="legacy_order_resolved"):
        if resolution.details.get("outcome") == "link":
            aliases[resolution.details["broker_order_id"]] = resolution.details["order_log_id"]
    for result in results:
        try:
            oid = audit_id(result, settings)
            row = db.query(OrderLog).filter_by(order_id=oid).first()
            legacy_row = db.get(OrderLog, aliases[oid]) if oid in aliases else None
            if row is None and legacy_row is not None:
                row = legacy_row
            if row is None:
                if result.quantity is None or result.quantity <= 0:
                    raise ExecutionBlockedError("original_order_quantity_missing")
                row = OrderLog(order_id=oid, account_key=key, environment=execution_environment(settings),
                    strategy_id="external_mts", ticker=result.ticker, side=result.side.value,
                    qty=result.quantity, fill_qty=0, status="pending", broker=settings.maps_broker_mode,
                    mode="mock" if settings.is_paper_account else "live", order_price=result.order_price)
                db.add(row)
                db.flush()
            updated += apply_result(db, row, result)
            if legacy_row is not None and legacy_row.id != row.id:
                updated += apply_result(db, legacy_row, result)
            seen.add(oid)
            orders[oid] = {"ticker": result.ticker, "side": result.side.value,
                "qty": result.filled_quantity, "notional": str(money(result.filled_quantity) * money(result.avg_price))}
        except (ExecutionBlockedError, ValueError) as exc:
            reasons.append(getattr(exc, "reason_code", "invalid_order_evidence"))
    db.flush()
    for intent in intents:
        if intent.status == "UNKNOWN":
            reasons.append("order_outcome_unknown")
        elif intent.status in ("ACKNOWLEDGED", "PARTIALLY_FILLED") and intent.broker_order_id not in seen:
            reasons.append("active_order_missing")
    legacy = db.query(OrderLog).filter(OrderLog.account_key.is_(None), OrderLog.status.in_(["pending", "partially_filled", "unknown"])).first()
    if legacy:
        reasons.append("legacy_order_requires_resolution")

    if snapshot is not None:
        try:
            nav, cash = money(snapshot.balance.total_value), money(snapshot.balance.cash)
            if nav <= 0 or cash < 0:
                raise ExecutionBlockedError("invalid_account_valuation")
            activity = broker.get_account_activity(first.ref_date if first else today, today)
            if not activity.complete or activity.costs is None:
                raise ExecutionBlockedError("account_costs_unavailable")
            if any(p.quantity < 0 or money(p.market_value) < 0 for p in snapshot.positions.values()):
                raise ExecutionBlockedError("invalid_position_evidence")
            holdings = {t: {"qty": p.quantity, "value": str(money(p.market_value))} for t, p in snapshot.positions.items()}
            evidence = {"positions": holdings, "orders": orders, "costs": str(activity.costs),
                "unsupported": list(activity.unsupported), "adjustment_timing": "end_of_interval"}
            observation = AccountObservation(account_key=key, observed_at=now, ref_date=today,
                nav=nav, cash=cash, evidence=evidence, complete=False)
            db.add(observation)
            db.flush()
            unresolved = db.query(AccountAdjustment).filter_by(account_key=key, status="unclassified").first()
            adjustments = db.query(AccountAdjustment).filter(
                AccountAdjustment.account_key == key, AccountAdjustment.status == "confirmed",
                AccountAdjustment.observation_id > (baseline.id if baseline else 0)).all()
            flow = sum((a.amount for a in adjustments if a.kind in ("deposit", "withdrawal", "security_transfer")), Decimal(0))
            cash_adjustment = sum((a.amount for a in adjustments if a.kind != "security_transfer"), Decimal(0))
            if baseline:
                old = baseline.evidence
                expected_cash = money(baseline.cash) - (money(activity.costs) - money(old["costs"]))
                expected_qty = {t: p["qty"] for t, p in old["positions"].items()}
                for oid, row in orders.items():
                    prior = old["orders"].get(oid, {"qty": 0, "notional": "0"})
                    delta = row["qty"] - prior["qty"]
                    if delta < 0:
                        raise ExecutionBlockedError("fill_quantity_regressed")
                    sign = 1 if row["side"] == "buy" else -1
                    expected_qty[row["ticker"]] = expected_qty.get(row["ticker"], 0) + sign * delta
                    expected_cash -= sign * (money(row["notional"]) - money(prior["notional"]))
                for adjustment in adjustments:
                    if adjustment.kind == "security_transfer":
                        for ticker, quantity in adjustment.resolution.get("quantity_changes", {}).items():
                            expected_qty[ticker] = expected_qty.get(ticker, 0) + quantity
                quantity_diff = {t: holdings.get(t, {}).get("qty", 0) - expected_qty.get(t, 0)
                    for t in set(holdings) | set(expected_qty)
                    if holdings.get(t, {}).get("qty", 0) != expected_qty.get(t, 0)}
                difference = cash - expected_cash - cash_adjustment
                if abs(difference) > Decimal("1") or quantity_diff:
                    reasons.append("account_difference_unclassified")
                    if unresolved is None:
                        db.add(AccountAdjustment(account_key=key, observation_id=observation.id,
                            amount=difference, evidence={"cash_difference": str(difference), "quantity_changes": quantity_diff},
                            status="unclassified", created_at=now))
                if not reasons and unresolved is None:
                    adjusted_nav = nav - flow
                    if adjusted_nav <= 0:
                        raise ExecutionBlockedError("adjusted_nav_invalid")
                    state.value_index = money(state.value_index) * adjusted_nav / money(baseline.nav)
            else:
                state.value_index = state.high_water = state.day_open_index = Decimal(1)
            if unresolved:
                reasons.append("account_difference_unclassified")
            observation.complete = not reasons
            if observation.complete:
                if state.ref_date != today and baseline:
                    state.day_open_index = money(baseline.evidence.get("value_index", state.value_index))
                state.ref_date = today
                state.high_water = max(money(state.high_water), money(state.value_index))
                state.daily_return = money(state.value_index) / money(state.day_open_index) - 1
                state.drawdown = 1 - money(state.value_index) / money(state.high_water)
                observation.evidence = {**evidence, "value_index": str(state.value_index)}
                if state.daily_return <= -money(settings.daily_loss_limit) or state.drawdown >= money(settings.maps_account_mdd_limit):
                    if not state.killed:
                        db.add(KillSwitchLog(strategy_id=f"account:{key[:16]}", account_key=key,
                            scope="account", event_type="trigger", reason="account_loss_limit", value="Account loss limit reached"))
                        event(db, key, "account_loss_limit")
                    state.killed = True
                if not baseline or (first and first.ref_date == today):
                    reasons.append("account_warmup")
        except (BrokerAdapterError, NotImplementedError, ValueError, ArithmeticError) as exc:
            reasons.append(getattr(exc, "reason_code", "account_activity_incomplete"))
    if state.killed:
        reasons.append("account_kill_switch")
    state.checked_at = now
    state.version += 1
    state.block_reasons = sorted(set(reasons))
    state.status = "READY" if not reasons else "WARMUP" if reasons == ["account_warmup"] else "BLOCKED"
    if not reasons:
        state.last_complete_at = now
    if previous_status != (state.status, state.block_reasons):
        event(db, key, "account_state_changed", {"status": state.status, "reasons": state.block_reasons})
    db.commit()
    balance = snapshot.balance if snapshot else None
    return {"cash": balance.cash if balance else None, "positions_value": balance.positions_value if balance else None,
        "total_assets": balance.total_value if balance else None, "open_orders": len(open_orders),
        "updated_orders": updated, "expired_orders": 0, "sync_errors": len(set(reasons)),
        "complete": not reasons, "checked_at": now.isoformat(),
        "last_complete_at": state.last_complete_at.isoformat() if state.last_complete_at else None,
        "block_reasons": state.block_reasons}, snapshot, open_orders
