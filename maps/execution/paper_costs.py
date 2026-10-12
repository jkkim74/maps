"""Evidence-backed KIS paper account costs and atomic operator settlements.

No function in this module calls a broker, submits an order or enables trading.
Account totals never manufacture order-level fees or taxes.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from maps.common.exceptions import ExecutionBlockedError
from maps.common.models import (
    AccountAdjustment, AccountObservation, ExecutionAccountState, ExecutionSafetyEvent,
    OrderIntent, OrderLog, PaperCostEvidence, PaperCostSettlement,
)
from maps.execution.safety import account_execution_lock, account_key, execution_environment, utcnow

KST = dt.timezone(dt.timedelta(hours=9))
ZERO = Decimal(0)


def amount(value: Any) -> Decimal:
    """Reject unknown/nonfinite amounts instead of converting them to zero."""
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ExecutionBlockedError("cost_amount_invalid") from exc
    if not result.is_finite():
        raise ExecutionBlockedError("cost_amount_invalid")
    return result


def digest(value: Any) -> str:
    """Stable semantic hash; numeric strings retain decimal precision."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def is_paper(settings: Any) -> bool:
    """Restrict this accounting source to the actual KIS paper environment."""
    return execution_environment(settings) == "kis_paper"


def record_cost_evidence(db: Session, key: str, kind: str, ref_date: dt.date,
                         value: Decimal | None, payload: dict, now: dt.datetime) -> PaperCostEvidence:
    """Append immutable deduplicated evidence, leaving transaction ownership outside."""
    identity = digest({"kind": kind, "date": ref_date, "amount": value,
                       "payload": {k: v for k, v in payload.items() if k != "observed_at"}})
    existing = db.query(PaperCostEvidence).filter_by(account_key=key, fingerprint=identity).first()
    if existing is not None:
        return existing
    prior = db.query(PaperCostEvidence).filter_by(account_key=key, kind=kind, ref_date=ref_date).order_by(PaperCostEvidence.id.desc()).first()
    row = PaperCostEvidence(account_key=key, environment="kis_paper", kind=kind,
        ref_date=ref_date, observed_at=now, amount=value, fingerprint=identity, payload=payload,
        supersedes_id=prior.id if prior else None)
    db.add(row); db.flush()
    return row


def _rows(db: Session, key: str, baseline: AccountObservation) -> tuple[list, list]:
    adjustments = db.query(AccountAdjustment).filter(AccountAdjustment.account_key == key,
        AccountAdjustment.status == "confirmed", AccountAdjustment.observation_id > baseline.id).all()
    receipts = [r for r in db.query(PaperCostSettlement).filter_by(account_key=key).all()
                if r.payload.get("baseline_id") == baseline.id]
    return adjustments, receipts


def breakdown(db: Session, key: str, baseline: AccountObservation | None,
              current: AccountObservation) -> dict:
    """Reconstruct the latest residual including all confirmed interval deductions."""
    if baseline is None:
        return {"available": False, "reason": "account_baseline_missing"}
    old, new = baseline.evidence, current.evidence
    adjustments, receipts = _rows(db, key, baseline)
    broker_cost = amount(new["costs"]) - amount(old["costs"])
    expected = amount(baseline.cash) - broker_cost
    quantities = {ticker: p["qty"] for ticker, p in old["positions"].items()}
    trades = []
    for oid, order in new["orders"].items():
        prior = old["orders"].get(oid, {"qty": 0, "notional": "0"})
        sign = 1 if order["side"] == "buy" else -1
        expected -= sign * (amount(order["notional"]) - amount(prior["notional"]))
        quantities[order["ticker"]] = quantities.get(order["ticker"], 0) + sign * (order["qty"] - prior["qty"])
        trades.append({"broker_order_id": oid, **order})
    for adjustment in adjustments:
        if adjustment.kind == "security_transfer":
            for ticker, qty in (adjustment.resolution or {}).get("quantity_changes", {}).items():
                quantities[ticker] = quantities.get(ticker, 0) + qty
        else:
            expected += adjustment.amount
    settled = sum((amount(r.payload["deduction"]) for r in receipts), ZERO)
    expected -= settled
    changes = {t: new["positions"].get(t, {}).get("qty", 0)-quantities.get(t, 0)
               for t in set(quantities) | set(new["positions"])
               if new["positions"].get(t, {}).get("qty", 0) != quantities.get(t, 0)}
    return {"available": True, "baseline_id": baseline.id, "observation_id": current.id,
        "period_start": str(baseline.ref_date), "period_end": str(current.ref_date),
        "baseline_observed_at": baseline.observed_at.isoformat()+"Z",
        "observed_at": current.observed_at.isoformat()+"Z",
        "expected_cash": str(expected), "actual_cash": str(current.cash),
        "residual": str(amount(current.cash)-expected), "quantity_changes": changes,
        "broker_cost_delta": str(broker_cost), "settled_costs": str(settled), "trades": trades,
        "confirmed_adjustments": [{"id": a.id, "kind": a.kind, "amount": str(a.amount),
                                   "version": a.version} for a in adjustments],
        "settlements": [{"id": r.id, "deduction": r.payload["deduction"]} for r in receipts]}


def _context(db: Session, settings: Any) -> tuple:
    key = account_key(settings)
    state = db.get(ExecutionAccountState, key)
    current = db.query(AccountObservation).filter_by(account_key=key).order_by(AccountObservation.id.desc()).first()
    baseline = db.query(AccountObservation).filter_by(account_key=key, complete=True).order_by(AccountObservation.id.desc()).first()
    return key, state, baseline, current


def settlement_overview(db: Session, settings: Any) -> dict:
    """Read-only operator view; GET never refreshes the broker or writes evidence."""
    key, state, baseline, current = _context(db, settings)
    details = breakdown(db, key, baseline, current) if current else {"available": False, "reason": "account_unobserved"}
    pending = db.query(AccountAdjustment).filter_by(account_key=key, status="unclassified").all()
    return {"supported": is_paper(settings), "mode": settings.maps_paper_cost_reconciliation_mode,
        "version": state.version if state else None, "breakdown": details,
        "cost_coverage": (current.evidence.get("paper_costs", {}) if current else {}),
        "order_cost_candidates": _order_candidates(db, key) if is_paper(settings) else [],
        "pending_adjustments": [{"id": a.id, "amount": str(a.amount), "version": a.version,
                                 "evidence": a.evidence} for a in pending]}


def _account_proof(db: Session, key: str, evidence_id: int | None) -> tuple:
    """Read a paid interval and its original fill identities, including after rollover."""
    proof = db.get(PaperCostEvidence, evidence_id) if evidence_id else None
    if proof is None or proof.account_key != key or proof.kind not in ("account_statement", "balance"):
        raise ExecutionBlockedError("account_cost_coverage_required")
    receipts = [r for r in db.query(PaperCostSettlement).filter_by(account_key=key)
                if proof.id in r.payload.get("evidence_ids", []) and
                (proof.kind == "account_statement" or r.payload.get("source") == "kis_balance")]
    if len(receipts) != 1:
        raise ExecutionBlockedError("account_cost_coverage_required")
    receipt = receipts[0]
    baseline = db.get(AccountObservation, receipt.payload.get("baseline_id"))
    through = db.get(AccountObservation, receipt.payload.get("through_observation_id"))
    if baseline is None or through is None or baseline.account_key != key or through.account_key != key:
        raise ExecutionBlockedError("account_cost_coverage_required")
    total = amount(proof.amount if proof.kind == "account_statement" else receipt.payload["deduction"])
    return proof, baseline, through, total


def _order_candidates(db: Session, key: str) -> list[dict]:
    """Offer persisted fully covered orders; no rate-based fee allocation."""
    candidates = {}
    proof_ids = {evidence_id for receipt in db.query(PaperCostSettlement).filter_by(account_key=key)
                 for evidence_id in receipt.payload.get("evidence_ids", [])}
    for proof_id in sorted(proof_ids):
        try:
            proof, baseline, through, _ = _account_proof(db,key,proof_id)
        except ExecutionBlockedError:
            continue
        for oid, order in through.evidence["orders"].items():
            if baseline.evidence["orders"].get(oid, {}).get("qty", 0):
                continue
            candidates[oid] = {**order, "broker_order_id":oid,"account_evidence_id":proof.id,
                "period_start":str(baseline.ref_date),"period_end":str(through.ref_date)}
    return list(candidates.values())


def _semantic(db: Session, key: str, baseline: AccountObservation, current: AccountObservation,
              details: dict, state: ExecutionAccountState) -> dict:
    pending = db.query(AccountAdjustment).filter_by(account_key=key, status="unclassified").order_by(AccountAdjustment.id).all()
    intents = db.query(OrderIntent).filter_by(account_key=key).filter(OrderIntent.status.in_(("UNKNOWN", "SENDING"))).all()
    return {"key": key, "baseline": baseline.id, "date": str(current.ref_date), "cash": str(current.cash),
        "quantities": {t: p["qty"] for t, p in current.evidence["positions"].items()},
        "orders": current.evidence["orders"], "costs": current.evidence["costs"],
        "cost_observation": {k: v for k,v in current.evidence.get("cost_observation", {}).items() if k != "observed_at"},
        "adjustments": details["confirmed_adjustments"], "settlements": details["settlements"],
        "pending": [(a.id, a.version, str(a.amount)) for a in pending],
        "unknown": [(i.id, i.version) for i in intents], "killed": state.killed,
        "blocking": sorted(state.block_reasons or [])}


def _validate_statement(item: dict, details: dict) -> Decimal:
    """Require statement provenance, bounded interval and explicit actual amounts."""
    source = item.get("source_url", "")
    url = urlparse(source)
    if url.scheme != "https" or not url.netloc or url.username or url.password or not re.fullmatch(r"[0-9a-fA-F]{64}", item.get("document_hash", "")):
        raise ExecutionBlockedError("broker_statement_provenance_required")
    value = amount(item.get("amount"))
    if value < 0:
        raise ExecutionBlockedError("cost_amount_invalid")
    try:
        start, end = dt.date.fromisoformat(str(item["period_start"])), dt.date.fromisoformat(str(item["period_end"]))
    except (KeyError, ValueError) as exc:
        raise ExecutionBlockedError("statement_period_invalid") from exc
    if start > end or (item.get("scope") == "account" and
            (str(start) < details["period_start"] or str(end) > details["period_end"])):
        raise ExecutionBlockedError("statement_period_invalid")
    return value


def _fingerprint(db: Session, key: str, baseline: AccountObservation, current: AccountObservation,
                 details: dict, state: ExecutionAccountState, request: dict) -> str:
    """Include selected historical order ledgers as well as current account semantics."""
    from maps.common.models import FujimotoOrder
    order_state = []
    for item in request.get("items", []):
        if item.get("scope") != "order":
            continue
        oid = item.get("broker_order_id")
        row = db.query(OrderLog).filter_by(account_key=key, order_id=oid).first()
        fuji = db.query(FujimotoOrder).filter_by(account_key=key, broker_order_id=oid).first()
        order_state.append({"id":oid,"log": [row.status,row.fill_qty] if row else None,
            "ledger": [fuji.status,fuji.filled_quantity,str(fuji.gross),str(fuji.fees),str(fuji.tax)] if fuji else None})
    return digest({"context":_semantic(db,key,baseline,current,details,state),
        "order_state":order_state,"reason":request.get("reason"),"items":request.get("items")})


def preview_settlement(db: Session, settings: Any, request: dict) -> dict:
    """Validate a complete settlement without modifying the database."""
    if not is_paper(settings):
        raise ExecutionBlockedError("paper_environment_required")
    key, state, baseline, current = _context(db, settings)
    if state is None or baseline is None or current is None:
        raise ExecutionBlockedError("account_baseline_missing")
    if not 0 <= (utcnow()-current.observed_at).total_seconds() <= settings.maps_execution_reconciliation_max_age_seconds:
        raise ExecutionBlockedError("reconciliation_stale")
    details = breakdown(db, key, baseline, current)
    if details["quantity_changes"]:
        raise ExecutionBlockedError("quantity_difference_requires_classification")
    if set(state.block_reasons or []) - {"account_difference_unclassified", "paper_costs_missing", "paper_costs_unstable", "paper_costs_coverage_missing", "paper_costs_overlap", "paper_costs_corrected", "paper_costs_cash_mismatch", "paper_costs_order_gross_missing", "paper_costs_observe"}:
        raise ExecutionBlockedError("account_not_ready_for_settlement")
    if db.query(OrderIntent).filter_by(account_key=key).filter(OrderIntent.status.in_(("UNKNOWN", "SENDING"))).first():
        raise ExecutionBlockedError("account_unknown_order")
    items = request.get("items", [])
    if not items or len(items) > 100 or len(request.get("reason", "").strip()) < 8:
        raise ExecutionBlockedError("statement_items_required")
    accounts = [i for i in items if i.get("scope") == "account"]
    # One interval statement avoids guessing overlaps between several day totals.
    if len(accounts) > 1:
        raise ExecutionBlockedError("single_account_statement_required")
    for item in items:
        _validate_statement(item, details)
        if item.get("scope") not in ("account", "order"):
            raise ExecutionBlockedError("statement_scope_invalid")
    adjustments, receipts = _rows(db, key, baseline)
    deduction = ZERO
    if accounts:
        account = accounts[0]
        if (str(account["period_start"]), str(account["period_end"])) != (details["period_start"], details["period_end"]):
            raise ExecutionBlockedError("statement_full_interval_required")
        included = account.get("included_adjustment_ids", [])
        fees = [a for a in adjustments if a.kind == "fee"]
        if len(included) != len(set(included)) or set(included) != {a.id for a in fees}:
            raise ExecutionBlockedError("legacy_cost_overlap_requires_mapping")
        included_receipts = account.get("included_settlement_ids", [])
        if len(included_receipts) != len(set(included_receipts)) or set(included_receipts) != {r.id for r in receipts}:
            raise ExecutionBlockedError("settled_cost_overlap_requires_mapping")
        deduction = amount(account["amount"]) + sum((a.amount for a in fees), ZERO) - amount(details["settled_costs"])
        if deduction < 0:
            raise ExecutionBlockedError("cost_correction_requires_review")
    order_ids = set()
    intervals = [(baseline, current, amount(accounts[0]["amount"]))] if accounts else []
    for item in (i for i in items if i.get("scope") == "order"):
        oid = item.get("broker_order_id")
        if oid in order_ids:
            raise ExecutionBlockedError("duplicate_order_cost")
        order_ids.add(oid)
        row = db.query(OrderLog).filter_by(account_key=key, order_id=oid).first()
        if (row is None or row.status not in ("filled", "cancelled", "rejected") or not row.fill_qty
                or item.get("fees") is None or item.get("tax") is None or item.get("gross") is None):
            raise ExecutionBlockedError("exact_order_cost_required")
        fees, tax, gross = amount(item["fees"]), amount(item["tax"]), amount(item["gross"])
        if accounts:
            proof_baseline, proof_current, covered_total = baseline, current, amount(accounts[0]["amount"])
        else:
            _, proof_baseline, proof_current, covered_total = _account_proof(db,key,item.get("account_evidence_id"))
            intervals.append((proof_baseline, proof_current, covered_total))
        if (str(item["period_start"]) < str(proof_baseline.ref_date)
                or str(item["period_end"]) > str(proof_current.ref_date)):
            raise ExecutionBlockedError("account_cost_coverage_required")
        if proof_baseline.evidence["orders"].get(oid, {}).get("qty", 0):
            raise ExecutionBlockedError("order_cost_partial_interval_requires_review")
        recorded = proof_current.evidence["orders"].get(oid)
        if (min(fees, tax) < 0 or gross <= 0 or fees+tax != amount(item["amount"])
                or recorded is None or recorded["qty"] != row.fill_qty or amount(recorded["notional"]) != gross):
            raise ExecutionBlockedError("order_cost_identity_mismatch")
    # A replacement document is not a new cost budget. Count the latest actual
    # costs for every fully covered order, across evidence IDs and this whole draft.
    allocations = {proof.payload["broker_order_id"]: amount(proof.amount)
        for proof in db.query(PaperCostEvidence).filter_by(account_key=key, kind="order_statement")
        .order_by(PaperCostEvidence.id)}
    allocations.update({item["broker_order_id"]: amount(item["amount"])
                        for item in items if item["scope"] == "order"})
    for proof_baseline, proof_current, covered_total in intervals:
        covered_orders = {oid for oid, order in proof_current.evidence["orders"].items()
                          if order["qty"] > 0 and not proof_baseline.evidence["orders"].get(oid, {}).get("qty", 0)}
        if sum((allocations.get(oid, ZERO) for oid in covered_orders), ZERO) > covered_total:
            raise ExecutionBlockedError("order_cost_exceeds_account_total")
    remaining = amount(details["residual"])+deduction
    if accounts and abs(remaining) > 1:
        raise ExecutionBlockedError("statement_cash_mismatch")
    fingerprint = _fingerprint(db,key,baseline,current,details,state,request)
    return {"can_commit": True, "fingerprint": fingerprint, "version": state.version,
        "deduction": str(deduction), "remaining_residual": str(remaining), "breakdown": details,
        "message": "awaiting_reconciliation"}


def commit_settlement(db: Session, settings: Any, request: dict, actor: str,
                      *, owner_user_id: int | None) -> dict:
    """Commit evidence, ledger corrections and receipt in one account-locked transaction."""
    key = account_key(settings)
    identity = request.get("idempotency_key", "")
    if not isinstance(identity, str) or not 1 <= len(identity) <= 128:
        raise ExecutionBlockedError("idempotency_key_required")
    request_hash = digest(request)
    with account_execution_lock(key):
        try:
            db.expire_all()
            prior = db.query(PaperCostSettlement).filter_by(account_key=key, idempotency_key=identity).first()
            if prior is not None:
                if prior.request_hash != request_hash:
                    raise ExecutionBlockedError("idempotency_key_conflict")
                return prior.result
            _, state, baseline, current = _context(db,settings)
            if state is None or baseline is None or current is None:
                raise ExecutionBlockedError("settlement_changed")
            expected = _fingerprint(db,key,baseline,current,breakdown(db,key,baseline,current),state,request)
            if request.get("fingerprint") != expected:
                raise ExecutionBlockedError("settlement_changed")
            preview = preview_settlement(db, settings, request)
            if request.get("fingerprint") != preview["fingerprint"]:
                raise ExecutionBlockedError("settlement_changed")
            if not isinstance(request.get("version"), int) or not 1 <= request["version"] <= preview["version"]:
                raise ExecutionBlockedError("version_conflict")
            now = utcnow(); evidence_ids = []; account_evidence_id = None
            for item in sorted(request["items"], key=lambda i: i["scope"] != "account"):
                payload = {**item, "baseline_id": preview["breakdown"]["baseline_id"],
                    "through_observation_id": preview["breakdown"]["observation_id"], "actor": actor,
                    "reason": request["reason"]}
                if item["scope"] == "order":
                    payload["account_evidence_id"] = account_evidence_id or item.get("account_evidence_id")
                evidence = record_cost_evidence(db,key,item["scope"]+"_statement",
                    dt.date.fromisoformat(str(item["period_end"])),amount(item["amount"]),payload,now)
                evidence_ids.append(evidence.id)
                if item["scope"] == "account":
                    account_evidence_id = evidence.id
                else:
                    from maps.common.models import FujimotoOrder
                    from maps.fujimoto.service import FujimotoService
                    order = db.query(FujimotoOrder).filter_by(account_key=key, broker_order_id=item["broker_order_id"]).first()
                    if order is not None:
                        FujimotoService(db, settings=settings).settle_costs(key,owner_user_id,order.id,
                            gross=amount(item["gross"]),fees=amount(item["fees"]),tax=amount(item["tax"]),
                            evidence={"broker_order_id":item["broker_order_id"],"source_url":item["source_url"],
                                      "document_hash":item["document_hash"],"paper_cost_evidence_id":evidence.id},
                            commit=False)
            if account_evidence_id:
                _resolve_adjustments(db,key,evidence_ids,now)
            result = {"status": "awaiting_reconciliation", "evidence_ids": evidence_ids,
                      "deduction": preview["deduction"], "remaining_residual": preview["remaining_residual"]}
            receipt = PaperCostSettlement(account_key=key,idempotency_key=identity,request_hash=request_hash,
                created_at=now,actor=actor,payload={"baseline_id":preview["breakdown"]["baseline_id"],
                "through_observation_id":preview["breakdown"]["observation_id"],
                "deduction":preview["deduction"],"request":request,"evidence_ids":evidence_ids},result=result)
            db.add(receipt)
            db.add(ExecutionSafetyEvent(account_key=key,reason_code="paper_costs_settled",
                details={"actor":actor,"evidence_ids":evidence_ids,"deduction":preview["deduction"]},created_at=now))
            db.commit()
            return result
        except Exception:
            db.rollback()
            raise


def _resolve_adjustments(db: Session, key: str, evidence_ids: list[int], now: dt.datetime) -> None:
    """Close explained historical markers without a second cash deduction."""
    for row in db.query(AccountAdjustment).filter_by(account_key=key,status="unclassified"):
        if row.evidence.get("quantity_changes"):
            raise ExecutionBlockedError("quantity_difference_requires_classification")
        row.status = "costs_resolved"
        row.version += 1
        row.resolution = {"cost_evidence_ids": evidence_ids, "resolved_at": now.isoformat(),
                          "cash_effect": "account_settlement_only"}


def _balance_signature(observation: AccountObservation) -> str:
    """Stability ignores valuation changes and receipt timestamps, never fills/cash."""
    e = observation.evidence
    cost = e.get("cost_observation") or {}
    return digest({"date":observation.ref_date,"cash":str(amount(observation.cash).normalize()),
        "positions":{t:p["qty"] for t,p in e["positions"].items()},
        "orders":{oid:{"ticker":o["ticker"],"side":o["side"],"qty":o["qty"],
                       "notional":str(amount(o["notional"]).normalize())} for oid,o in e["orders"].items()},
        "costs":e["costs"],"cost_observation":{k:v for k,v in cost.items() if k != "observed_at"}})


def _cost_delta(baseline: AccountObservation, current: AccountObservation, settings: Any) -> Decimal:
    """Prove the interval from dated actual observations, never guess missing days."""
    old, new = baseline.evidence.get("cost_observation") or {}, current.evidence.get("cost_observation") or {}
    if not old.get("valid") or old.get("ref_date") != str(baseline.ref_date):
        raise ExecutionBlockedError("paper_costs_coverage_missing")
    old_total, new_total = amount(old.get("today_total")), amount(new.get("today_total"))
    if baseline.ref_date == current.ref_date:
        delta = new_total-old_total
        if delta < 0:
            raise ExecutionBlockedError("paper_costs_corrected")
        return delta
    # An observed and stable closing balance covers its own completed day only.
    # A missing trading day's closing observation is not reconstructed from bfdy.
    stamp = baseline.observed_at.replace(tzinfo=dt.timezone.utc).astimezone(KST)
    if (baseline.ref_date > current.ref_date or stamp.date() != baseline.ref_date or stamp.hour < 18
            or not baseline.evidence.get("paper_costs",{}).get("stable")):
        raise ExecutionBlockedError("paper_costs_coverage_missing")
    from maps.market.trading_rules import is_krx_closed_date
    day = baseline.ref_date+dt.timedelta(days=1)
    if (current.ref_date-day).days > 14:
        raise ExecutionBlockedError("paper_costs_coverage_missing")
    while day < current.ref_date:
        if not is_krx_closed_date(day,extra_closed_dates=tuple(settings.krx_closed_dates)):
            raise ExecutionBlockedError("paper_costs_coverage_missing")
        day += dt.timedelta(days=1)
    return new_total


def reconcile_costs(db: Session, settings: Any, baseline: AccountObservation | None,
                    current: AccountObservation, reasons: list[str], open_orders: list) -> dict:
    """Observe actual totals and apply only independently explained stable deltas."""
    key = current.account_key
    result = {"breakdown":breakdown(db,key,baseline,current),
              "coverage":{"complete":False,"stable":False,"reason":"paper_costs_missing"}}
    coverage = result["coverage"]
    cost = current.evidence.get("cost_observation") or {}
    try:
        value = amount(cost.get("today_total"))
        valid = cost.get("valid") is True and value >= 0 and cost.get("ref_date") == str(current.ref_date)
    except ExecutionBlockedError:
        value, valid = None, False
    evidence = record_cost_evidence(db,key,"balance",current.ref_date,value if valid else None,cost,current.observed_at)
    coverage["evidence_id"] = evidence.id
    prior = db.query(AccountObservation).filter(AccountObservation.account_key==key,
        AccountObservation.observed_at < current.observed_at).order_by(AccountObservation.observed_at.desc(),AccountObservation.id.desc()).first()
    stable = bool(prior and 0 < (current.observed_at-prior.observed_at).total_seconds()
                  <= settings.maps_execution_reconciliation_max_age_seconds
                  and _balance_signature(prior)==_balance_signature(current))
    coverage["stable"] = stable
    if baseline is not None and not reasons and not open_orders and not result["breakdown"]["quantity_changes"]:
        _, receipts = _rows(db,key,baseline)
        covered = [db.get(AccountObservation,r.payload.get("through_observation_id")) for r in receipts
            if r.payload.get("source") == "kis_balance" or any(
                (proof := db.get(PaperCostEvidence, evidence_id)) is not None
                and proof.account_key == key and proof.kind == "account_statement"
                for evidence_id in r.payload.get("evidence_ids", []))]
        if baseline.evidence.get("paper_costs",{}).get("complete"):
            covered.append(baseline)
        if (abs(amount(result["breakdown"]["residual"]))<=1 and stable
                and any(row is not None and _balance_signature(row)==_balance_signature(current) for row in covered)):
            coverage.update(complete=True,reason="settled_interval_unchanged")
            return result
    if not valid or baseline is None:
        return result
    try:
        delta = _cost_delta(baseline,current,settings)
    except ExecutionBlockedError as exc:
        coverage["reason"] = str(exc)
        return result
    adjustments, receipts = _rows(db,key,baseline)
    if any(a.kind=="fee" for a in adjustments):
        # Statement settlement explicitly maps legacy fees; totals cannot silently consume them.
        coverage["reason"] = "paper_costs_overlap"
        return result
    outstanding = delta-amount(result["breakdown"]["settled_costs"])
    coverage["observed_delta"] = str(delta)
    if outstanding < 0:
        coverage["reason"] = "paper_costs_corrected"
        return result
    if reasons or open_orders or result["breakdown"]["quantity_changes"]:
        coverage["reason"] = "paper_costs_account_incomplete"
        return result
    if any(o.get("gross_source") != "reported_total" for oid,o in current.evidence["orders"].items()
           if o != baseline.evidence["orders"].get(oid)):
        coverage["reason"] = "paper_costs_order_gross_missing"
        return result
    if not stable:
        coverage["reason"] = "paper_costs_unstable"
        return result
    if abs(amount(result["breakdown"]["residual"])+outstanding)>1:
        coverage["reason"] = "paper_costs_cash_mismatch"
        return result
    if outstanding and settings.maps_paper_cost_reconciliation_mode != "auto":
        coverage["reason"] = "paper_costs_observe"
        return result
    if outstanding:
        identity = "auto:"+digest({"baseline":baseline.id,"balance":_balance_signature(current),"delta":str(delta)})
        if not db.query(PaperCostSettlement).filter_by(account_key=key,idempotency_key=identity).first():
            payload = {"baseline_id":baseline.id,"through_observation_id":current.id,
                "deduction":str(outstanding),"evidence_ids":[evidence.id],"source":"kis_balance"}
            # Reconciliation persists diagnostics after a domain rejection. Keep
            # the cost mutation atomic so a rejected marker cannot leave a receipt.
            with db.begin_nested():
                db.add(PaperCostSettlement(account_key=key,idempotency_key=identity,request_hash=digest(payload),
                    created_at=current.observed_at,actor="system:paper_costs",payload=payload,
                    result={"status":"reconciled","deduction":str(outstanding)}))
                _resolve_adjustments(db,key,[evidence.id],current.observed_at)
                db.add(ExecutionSafetyEvent(account_key=key,reason_code="paper_costs_auto_settled",details=payload,
                                           created_at=current.observed_at))
                db.flush()
    result["breakdown"] = breakdown(db,key,baseline,current)
    coverage.update(complete=True,reason="actual_costs_reconciled")
    return result
