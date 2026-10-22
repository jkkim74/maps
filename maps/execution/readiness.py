"""Read persisted execution readiness without polling or changing broker state."""
from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from maps.common.models import ExecutionAccountState, KillSwitchLog
from maps.common.settings import MapsSettings, real_trading_unconfirmed
from maps.execution.safety import account_key, execution_environment


def utc_iso(value: dt.datetime | None) -> str | None:
    """Serialize database UTC-naive times with an explicit UTC offset."""
    if value is None:
        return None
    return value.replace(tzinfo=value.tzinfo or dt.timezone.utc).astimezone(dt.timezone.utc).isoformat()


def execution_readiness(db: Session, settings: MapsSettings, now: dt.datetime) -> dict[str, Any]:
    """Report account gates separately from per-strategy entry kills.

    READY describes saved account/configuration gates, not strategy eligibility or
    an authorization to send. The order manager still rechecks before submission.
    """
    key, environment = account_key(settings), execution_environment(settings)
    reasons: list[str] = []
    unknown: list[str] = []
    if not settings.maps_live_trading_enabled:
        reasons.append("trading_disabled")
    if settings.maps_dry_run:
        reasons.append("dry_run")
    if real_trading_unconfirmed(settings):
        reasons.append("real_trading_unconfirmed")
    if settings.maps_broker_mode == "kiwoom":
        reasons.append("broker_execution_unsupported")

    state = db.get(ExecutionAccountState, key)
    if state is None:
        unknown.append("account_unobserved")
    elif state.environment != environment:
        unknown.append("account_environment_mismatch")
    else:
        reasons.extend(state.block_reasons or [])
        if state.killed:
            reasons.append("account_kill_switch")
        if state.status in ("BLOCKED", "WARMUP"):
            reasons.append("account_warmup" if state.status == "WARMUP" else "account_blocked")
        elif state.status != "READY":
            unknown.append("account_status_unknown")
        checked = state.checked_at
        if checked is not None:
            checked = checked.replace(tzinfo=checked.tzinfo or dt.timezone.utc).astimezone(dt.timezone.utc)
            age = (now.replace(tzinfo=now.tzinfo or dt.timezone.utc) - checked).total_seconds()
        else:
            age = None
        if age is None or age < 0 or age > settings.maps_execution_reconciliation_max_age_seconds:
            unknown.append("reconciliation_stale")

    # Legacy strategy kills still block entry in RiskManager; never adopt their
    # unknown-account orders. No 200-row limit may hide a still-active kill.
    logs = (db.query(KillSwitchLog)
            .filter(or_(KillSwitchLog.account_key == key, KillSwitchLog.account_key.is_(None)))
            .order_by(KillSwitchLog.created_at.desc(), KillSwitchLog.id.desc()).all())
    latest: dict[tuple[str, str], KillSwitchLog] = {}
    for log in logs:
        is_account = log.scope == "account"
        if is_account and log.account_key != key:
            continue
        if not is_account and not log.strategy_id:
            continue
        scope = ("account", key) if is_account else ("strategy", log.strategy_id)
        latest.setdefault(scope, log)
    strategy_blocks: dict[str, list[str]] = {}
    for (scope, strategy), log in latest.items():
        if log.event_type not in ("trigger", "approved"):
            continue
        if scope == "account":
            reasons.append("account_kill_switch")
        else:
            strategy_blocks[strategy] = list(dict.fromkeys(["strategy_kill_switch", log.reason]))

    execution_state = "blocked" if reasons else "unknown" if unknown else "ready"
    return dict(execution_state=execution_state, block_reasons=list(dict.fromkeys(reasons + unknown)),
                checked_at=utc_iso(state.checked_at) if state and state.environment == environment else None,
                strategy_blocks=strategy_blocks, environment=environment)
