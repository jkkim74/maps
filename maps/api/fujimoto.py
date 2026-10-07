"""Admin and account-owner scoped controls; read views never contact the broker."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import or_
from sqlalchemy.orm import Session

from maps.api.auth import current_identity
from maps.api.deps import get_db
from maps.api.schemas import (FujimotoConfigRequest, FujimotoActivationRequest,
                              FujimotoComparabilityRequest, FujimotoCostRequest)
from maps.common.exceptions import DataQualityError, ExecutionBlockedError
from maps.common.models import FujimotoCycle, FujimotoEvidence, ValidationRun, ExecutionAccountState
from maps.execution.safety import account_key, utcnow
from maps.fujimoto.domain import Mode
from maps.fujimoto.repository import json_data
from maps.fujimoto.service import FujimotoService

router = APIRouter(prefix="/api/v1/fujimoto", tags=["Fujimoto"])


def scoped(request: Request, db: Session = Depends(get_db)) -> tuple:
    """Explicit owner filtering remains authoritative even for another administrator."""
    identity = current_identity(request)
    if not identity.is_admin:
        raise HTTPException(403, "administrator_required")
    service, key = FujimotoService(db), account_key()
    try:
        service.owner(key, identity.id)
    except ExecutionBlockedError as exc:
        raise HTTPException(403, str(exc)) from exc
    return service, key, identity.id


def mutation(fn, *args, **kwargs):
    """Expose domain guard reasons instead of treating rejection as a server error."""
    try:
        return fn(*args, **kwargs)
    except (ExecutionBlockedError, DataQualityError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc


def cycle_row(service, cycle) -> dict:
    """Return independent strategy ownership and provisional accounting labels."""
    gaps = service.unresolved_costs(cycle.account_key)
    provisional = any(o.id in gaps for o in service.repo.orders(cycle.account_key) if o.cycle_id == cycle.id)
    return {"id": cycle.id, "mode": cycle.mode, "ticker": cycle.ticker,
        "config_id": cycle.config_id, "budget": float(cycle.budget), "state": cycle.state,
        "cost_basis": float(cycle.cost_basis), "realized_pnl": float(cycle.realized_pnl),
        "accounting": "provisional_costs_missing" if provisional else "confirmed"}


@router.get("/status")
def status(scope=Depends(scoped)) -> dict:
    """Persisted status, defaults and actionable coverage/approval blocks."""
    service, key, _ = scope
    configs, control = service.current_configs(key), service.control(key)
    state = service.db.get(ExecutionAccountState, key)
    modes = []
    for mode in Mode:
        config = configs.get(mode.value)
        modes.append({"mode": mode.value, "strategy_id": mode.strategy_id,
            "budget": float(config.budget) if config else None,
            "config_id": config.id if config else None,
            "cash": float(service.repo.cash(config.id)) if config else None,
            "reserved_cash": float(service.repo.reserved_cash(config.id)) if config else 0,
            "settings": config.settings if config else {"with_orderbook": True},
            "cycles": [cycle_row(service, c) for c in service.repo.cycles(key, mode)]})
    screens = service.repo.evidence_as_of("screen", "*", utcnow())
    quality = service.repo.evidence_as_of("feed_quality", "*", utcnow(), account_key=key)
    reasons = list(state.block_reasons if state else ["account_not_reconciled"])
    if not configs:
        reasons.append("dedicated_budget_required")
    if not control.get("entries_enabled"):
        reasons.append("new_entries_stopped")
    if not service.settings.maps_fujimoto_enabled:
        reasons.append("fujimoto_runtime_disabled")
    if not screens:
        reasons.append("screening_coverage_missing")
    costs = service.unresolved_costs(key)
    if costs:
        reasons.append("actual_order_costs_unresolved")
    return {"control": control, "modes": modes, "block_reasons": reasons,
        "screen": screens[-1].payload if screens else None,
        "feed_quality": quality[-1].payload if quality else {"reason": "no_recorded_feed"},
        "unresolved_cost_orders": costs,
        "storage_limits": {"quote_rows": service.settings.maps_fujimoto_tape_rows,
                           "candidate_rows": service.settings.maps_fujimoto_candidate_rows}}


@router.put("/config")
def configure(payload: FujimotoConfigRequest, scope=Depends(scoped)) -> dict:
    """Apply explicit prospective budget/settings and stop new entries."""
    service, key, owner = scope
    return mutation(service.configure, key, owner, **payload.model_dump())


@router.post("/stop")
@router.post("/observe")
def stop(scope=Depends(scoped)) -> dict:
    """No pending-order precondition; retain reservations and approved exits."""
    service, key, owner = scope
    return mutation(service.stop, key, owner)


@router.post("/activate")
def activate(payload: FujimotoActivationRequest, scope=Depends(scoped)) -> dict:
    """Recompute current combined evidence outside the feed pump/account lock."""
    service, key, owner = scope
    return mutation(service.activate, key, owner, **payload.model_dump())


@router.get("/cycles/{cycle_id}")
def cycle(cycle_id: int, scope=Depends(scoped)) -> dict:
    """Cross-account or nonexistent cycle identities are indistinguishable."""
    service, key, _ = scope
    row = service.db.get(FujimotoCycle, cycle_id)
    if row is None or row.account_key != key:
        raise HTTPException(404, "cycle_not_found")
    return cycle_row(service, row)


@router.get("/evidence")
@router.get("/candidates")
def evidence(request: Request, kind: str | None = None, before: int | None = None,
             limit: int = Query(default=50, ge=1, le=200), scope=Depends(scoped)) -> list:
    """Paginated public source or owned-account evidence; compressed raw data stays on disk."""
    service, key, _ = scope
    kind = "candidate" if request.url.path.endswith("/candidates") else kind
    query = service.db.query(FujimotoEvidence).filter(or_(FujimotoEvidence.account_key == key,
                                                        FujimotoEvidence.account_key.is_(None)))
    if kind:
        query = query.filter(FujimotoEvidence.kind == kind)
    if before:
        query = query.filter(FujimotoEvidence.id < before)
    return [{"id": r.id, "kind": r.kind, "ticker": r.ticker,
        "observed_at": r.observed_at, "available_at": r.available_at,
        "fingerprint": r.fingerprint, "payload": {k: v for k, v in r.payload.items()
            if k not in {"raw_zlib", "inputs", "report"}}}
        for r in query.order_by(FujimotoEvidence.id.desc()).limit(limit)]


@router.get("/validation")
def validation(scope=Depends(scoped)) -> list:
    """Account-bound measured results only; query cannot create a pass."""
    service, key, _ = scope
    rows = service.db.query(ValidationRun).filter(ValidationRun.strategy_id.in_(
        [mode.strategy_id for mode in Mode]), ValidationRun.manifest["account_key"].as_string() == key).order_by(
            ValidationRun.created_at.desc()).limit(50).all()
    return [{"id": r.id, "strategy_id": r.strategy_id, "status": r.status,
             "run_date": r.run_date, "manifest": r.manifest, "metrics": r.metrics}
            for r in rows]


@router.post("/comparability")
def comparability(payload: FujimotoComparabilityRequest, scope=Depends(scoped)) -> dict:
    """Record actual source review now; never backdate to fiscal periods."""
    from maps.fujimoto.sources import record_comparability
    service, key, owner = scope
    row = mutation(record_comparability, service.db, **payload.model_dump(), owner=owner)
    return {"id": row.id, "available_at": row.available_at}


@router.post("/orders/{order_id}/costs")
def costs(order_id: int, payload: FujimotoCostRequest, scope=Depends(scoped)) -> dict:
    """Exact order settlement keeps ambiguous late corrections blocked for reconstruction."""
    service, key, owner = scope
    mutation(service.settle_costs, key, owner, order_id, **payload.model_dump())
    return {"status": "recorded"}
