"""Complete watchlist plans from owned terminal fills, never missing balances."""
from __future__ import annotations

from sqlalchemy.orm import Session

from maps.common.models import AnalysisPick, OrderIntent, OrderLog
from maps.execution.safety import utcnow


def completion_evidence(db: Session, pick: AnalysisPick) -> tuple[dict | None, str]:
    """Return exact linked evidence, or the reason closure remains unproven."""
    if pick.state != "BOUGHT" or not pick.exit_order_id:
        return None, "no_bought_exit"
    ids = {v for v in [pick.entry_order_id, pick.exit_order_id,
                       *(leg.order_id for leg in pick.legs)] if v}
    rows = db.query(OrderLog).filter(OrderLog.order_id.in_(ids)).all()
    if len(rows) != len(ids):
        return None, "linked_order_missing"
    buy_ids = {value for value in [pick.entry_order_id, *(leg.order_id for leg in pick.legs)] if value}
    if any(row.side != "buy" for row in rows if row.order_id in buy_ids):
        return None, "linked_order_direction_mismatch"
    exit_row = next((r for r in rows if r.order_id == pick.exit_order_id), None)
    if exit_row is None or exit_row.side != "sell" or exit_row.status != "filled":
        return None, "exit_not_filled"
    key, environment = exit_row.account_key, exit_row.environment
    if not key or not environment:
        return None, "order_account_unverified"
    intents = db.query(OrderIntent).filter_by(account_key=key, ticker=pick.ticker).all()
    owned_intents = [i for i in intents if i.request.get("source") == "analysis_pick"
                     and i.request.get("source_id") == pick.id]
    if any(i.status not in {"FILLED", "CANCELLED", "REJECTED"} for i in owned_intents):
        return None, "unresolved_order"
    if owned_intents:
        rows += db.query(OrderLog).filter(OrderLog.intent_id.in_([i.id for i in owned_intents])).all()
    if pick.trade_mode == "split":
        rows += db.query(OrderLog).filter(OrderLog.strategy_id.like(f"strategy_trade:{pick.id}:%")).all()
    rows = list({r.order_id: r for r in rows}.values())
    if any(r.account_key != key or r.environment != environment or r.ticker != pick.ticker
           or r.side not in {"buy", "sell"} for r in rows):
        return None, "order_identity_mismatch"
    if any(r.status not in {"filled", "cancelled", "rejected"} for r in rows):
        return None, "unresolved_order"
    if any(r.qty <= 0 or r.fill_qty is None or r.fill_qty < 0 or r.fill_qty > r.qty
           or (r.status == "filled" and r.fill_qty != r.qty) for r in rows):
        return None, "invalid_fill_quantity"
    buys = sum(r.fill_qty for r in rows if r.side == "buy")
    sells = sum(r.fill_qty for r in rows if r.side == "sell")
    if buys <= 0 or buys != sells:
        return None, "remaining_or_unverified_quantity"
    if pick.trade_mode == "split" and buys < sum(leg.filled_qty or 0 for leg in pick.legs):
        return None, "split_fill_history_incomplete"
    return {"pick_id": pick.id, "account_key": key, "order_ids": sorted(r.order_id for r in rows),
            "buy_filled": buys, "sell_filled": sells}, "complete"


def reconcile_completed_picks(db: Session, *, key: str | None = None,
                              pick_ids: list[int] | None = None, apply: bool = True) -> list[dict]:
    """Idempotently close proven plans in the caller transaction, without orders."""
    from maps.execution.reconciliation import event
    query = db.query(AnalysisPick).filter_by(state="BOUGHT")
    if pick_ids is not None:
        query = query.filter(AnalysisPick.id.in_(pick_ids))
    if apply:
        query = query.with_for_update()
    results = []
    for pick in query.all():
        evidence, reason = completion_evidence(db, pick)
        if evidence and key is not None and evidence["account_key"] != key:
            continue
        results.append({"pick_id": pick.id, "reason": reason, "eligible": evidence is not None})
        if not apply or evidence is None:
            continue
        pick.state = "CLOSED"
        pick.strategy_trade_enabled = False
        pick.exit_pending_reason = None
        pick.last_action_at = utcnow()
        event(db, evidence["account_key"], "analysis_pick_completed", {
            **evidence, "before": "BOUGHT", "after": "CLOSED"})
    return results
