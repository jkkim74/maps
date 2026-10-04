"""Evidence-backed resolution; no operation here sends or retries an order."""
from __future__ import annotations

import datetime as dt

from maps.common.exceptions import ExecutionBlockedError
from maps.common.models import AccountAdjustment, ExecutionAccountState, OrderIntent, OrderLog
from maps.execution.reconciliation import apply_result, audit_id, event, money
from maps.execution.safety import account_execution_lock, account_key, utcnow


def require_version(row, version):
    if row is None:
        raise ExecutionBlockedError("record_not_found")
    if row.version != version:
        raise ExecutionBlockedError("version_conflict")


def resolve_intent(db, broker, settings, intent_id, request, actor):
    key = account_key(settings)
    with account_execution_lock(key):
        db.expire_all()
        intent = db.get(OrderIntent, intent_id)
        require_version(intent, request.version)
        if intent.account_key != key or intent.status not in ("UNKNOWN", "SENDING"):
            raise ExecutionBlockedError("intent_not_resolvable")
        if request.outcome == "not_accepted":
            if not request.evidence.get("broker_nonacceptance_reference"):
                raise ExecutionBlockedError("broker_nonacceptance_evidence_required")
            intent.status = "REJECTED"
            intent.reserved_amount = 0
            intent.reserved_quantity = 0
        elif request.outcome == "link":
            if not request.broker_order_id:
                raise ExecutionBlockedError("broker_order_id_required")
            day = (intent.created_at + dt.timedelta(hours=9)).date()
            matches = [r for r in broker.get_order_history(day, day)
                       if audit_id(r, settings) == request.broker_order_id]
            if len(matches) != 1:
                raise ExecutionBlockedError("exact_broker_evidence_required")
            result = matches[0]
            if (result.ticker != intent.ticker or result.side.value != intent.side
                    or result.quantity != intent.quantity):
                raise ExecutionBlockedError("broker_identity_mismatch")
            row = db.query(OrderLog).filter_by(order_id=request.broker_order_id).first()
            if row is not None and row.intent_id not in (None, intent.id):
                raise ExecutionBlockedError("broker_order_already_linked")
            if row is None:
                row = OrderLog(order_id=request.broker_order_id, strategy_id=intent.strategy_id,
                    ticker=intent.ticker, side=intent.side, qty=intent.quantity,
                    fill_qty=0, status="pending", broker=settings.maps_broker_mode,
                    mode="mock" if settings.is_paper_account else "live")
                db.add(row)
            row.intent_id, row.account_key, row.environment = intent.id, key, intent.environment
            row.strategy_id = intent.strategy_id
            row.code_hash = intent.request.get("code_hash")
            row.params_hash = intent.request.get("params_hash")
            intent.broker_order_id = row.order_id
            apply_result(db, row, result)
        else:
            raise ExecutionBlockedError("invalid_resolution")
        intent.version += 1
        intent.updated_at = utcnow()
        event(db, key, "intent_resolved", {"intent_id": intent.id, "actor": actor,
            "reason": request.reason, "evidence": request.evidence, "outcome": request.outcome})
        db.commit()
        return {"id": intent.id, "status": intent.status, "version": intent.version}


def classify_adjustment(db, settings, adjustment_id, request, actor):
    key = account_key(settings)
    with account_execution_lock(key):
        db.expire_all()
        row = db.get(AccountAdjustment, adjustment_id)
        require_version(row, request.version)
        if row.account_key != key or row.status != "unclassified":
            raise ExecutionBlockedError("adjustment_not_resolvable")
        amount = money(request.amount)
        quantities = row.evidence.get("quantity_changes", {})
        if request.kind == "security_transfer":
            if not quantities or request.quantity_changes != quantities or row.amount != 0:
                raise ExecutionBlockedError("security_transfer_evidence_mismatch")
        elif quantities or amount != row.amount:
            raise ExecutionBlockedError("cash_adjustment_evidence_mismatch")
        if (request.kind == "deposit" and amount <= 0) or (request.kind in ("withdrawal", "fee") and amount >= 0):
            raise ExecutionBlockedError("adjustment_sign_invalid")
        row.kind, row.amount, row.status = request.kind, amount, "confirmed"
        row.resolution = {"actor": actor, "reason": request.reason, "evidence": request.evidence,
            "quantity_changes": request.quantity_changes, "confirmed_at": utcnow().isoformat()}
        row.version += 1
        event(db, key, "account_adjustment_classified", {"adjustment_id": row.id, **row.resolution})
        db.commit()
        return {"id": row.id, "status": row.status, "version": row.version}


def resolve_legacy_order(db, broker, settings, log_id, request, actor):
    """Associate legacy identity only after an explicit administrator review."""
    key = account_key(settings)
    with account_execution_lock(key):
        db.expire_all()
        state = db.get(ExecutionAccountState, key)
        require_version(state, request.version)
        row = db.get(OrderLog, log_id)
        if row is None or row.account_key is not None:
            raise ExecutionBlockedError("legacy_order_not_resolvable")
        if request.outcome == "not_accepted":
            if row.fill_qty or not request.evidence.get("broker_nonacceptance_reference"):
                raise ExecutionBlockedError("broker_nonacceptance_evidence_required")
            row.status = "rejected"
        else:
            day = (row.created_at + dt.timedelta(hours=9)).date()
            results = [r for r in broker.get_order_history(day, day)
                       if audit_id(r, settings) == request.broker_order_id]
            if len(results) != 1 or results[0].quantity != row.qty:
                raise ExecutionBlockedError("exact_broker_evidence_required")
            # Preserve the original audit id; only the scoped identity is linked.
            apply_result(db, row, results[0])
        row.account_key, row.environment = key, state.environment
        state.version += 1
        event(db, key, "legacy_order_resolved", {"order_log_id": row.id, "actor": actor,
            "reason": request.reason, "evidence": request.evidence, "outcome": request.outcome,
            "broker_order_id": request.broker_order_id})
        db.commit()
        return {"id": row.id, "status": row.status, "version": state.version}
