"""Operational configuration readiness API."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from maps.api.auth import current_identity
from maps.common.db import get_db
from maps.common.models import ExecutionPolicy
from maps.common.exceptions import ExecutionBlockedError
from maps.execution.runtime_policy import effective_policy
from maps.execution.safety import account_execution_lock, account_key, execution_environment, utcnow

from maps.api.schemas import (
    AIScoringModeResponse,
    AIScoringModeUpdate,
    BrokerHealthResponse,
    OpsConfigField,
    OpsConfigResponse,
    OpsConfigSection,
)
from maps.common.settings import (
    describe_trading_mode,
    get_config_status,
    get_missing_required_settings,
    get_settings,
    mask_config_value,
)

router = APIRouter(prefix="/api/v1/ops/config", tags=["Ops Config"])
_ENV_FILE = Path(".env")


class ExecutionPolicyUpdate(BaseModel):
    """Only paper Fujimoto execution may be enabled through the admin UI."""
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool
    scope: Literal["fujimoto_only"]
    expected_version: int = Field(ge=0, strict=True)


def _execution_view(db: Session) -> dict:
    """Effective policy plus independent account and environment guards."""
    from maps.execution.readiness import execution_readiness
    settings = get_settings()
    policy = effective_policy(settings, db)
    at = policy["updated_at"]
    if at is not None:
        policy["updated_at"] = at.replace(tzinfo=timezone.utc).isoformat()
    return {**policy, "environment": execution_environment(settings),
            "editable": settings.is_paper_account and not settings.kis_real_trading,
            "readiness": execution_readiness(db, settings, datetime.now(timezone.utc))}


@router.get("/execution")
def get_execution_policy(request: Request, db: Session = Depends(get_db)) -> dict:
    """Read effective permission without reconciliation or broker calls."""
    if not current_identity(request).is_admin:
        raise HTTPException(403, "administrator_required")
    try:
        return _execution_view(db)
    except ExecutionBlockedError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.put("/execution")
def update_execution_policy(update: ExecutionPolicyUpdate, request: Request,
                            db: Session = Depends(get_db)) -> dict:
    """Commit permission and audit together, serialized with broker dispatch."""
    identity = current_identity(request)
    if not identity.is_admin:
        raise HTTPException(403, "administrator_required")
    origin = request.headers.get("origin")
    expected = urlsplit(str(request.base_url))
    try:
        supplied = urlsplit(origin or "")
    except ValueError as exc:
        raise HTTPException(403, "same_origin_required") from exc
    if not origin or (supplied.scheme, supplied.netloc) != (expected.scheme, expected.netloc):
        raise HTTPException(403, "same_origin_required")
    settings = get_settings()
    if not settings.is_paper_account or settings.kis_real_trading:
        raise HTTPException(409, "paper_environment_required")
    from maps.execution.reconciliation import event
    key = account_key(settings)
    with account_execution_lock(key):
        try:
            previous = effective_policy(settings, db)
            if previous["version"] != update.expected_version:
                raise HTTPException(409, "execution_policy_version_conflict")
            row = db.get(ExecutionPolicy, key)
            if row is None:
                row = ExecutionPolicy(account_key=key, environment=execution_environment(settings))
                db.add(row)
            row.enabled, row.scope = update.enabled, update.scope
            row.version = previous["version"] + 1
            row.updated_by, row.updated_at = identity.username, utcnow()
            event(db, key, "execution_policy_changed", {
                "actor": identity.username,
                "before": {k: previous[k] for k in ("enabled", "scope", "version")},
                "after": {"enabled": row.enabled, "scope": row.scope, "version": row.version}})
            db.commit()
            return _execution_view(db)
        except ExecutionBlockedError as exc:
            db.rollback()
            raise HTTPException(503, str(exc)) from exc
        except SQLAlchemyError as exc:
            db.rollback()
            raise HTTPException(503, "execution_policy_save_failed") from exc


def _set_env_value(path: Path, key: str, value: str) -> None:
    """Atomically replace one plain dotenv value while preserving other lines."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    prefix = f"{key}="
    replacement = f"{prefix}{value}"
    updated = False
    for index, line in enumerate(lines):
        if line.startswith(prefix):
            lines[index] = replacement
            updated = True
            break
    if not updated:
        lines.append(replacement)

    fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as file:
            file.write("\n".join(lines) + "\n")
        os.replace(temp_name, path)
    except Exception:
        Path(temp_name).unlink(missing_ok=True)
        raise


@router.get("", response_model=OpsConfigResponse)
def get_ops_config(db: Session = Depends(get_db)) -> OpsConfigResponse:
    """Return masked external-integration configuration status."""
    settings = get_settings()
    policy = effective_policy(settings, db)
    settings = settings.model_copy(update={"maps_live_trading_enabled": policy["enabled"]})
    sections = [
        OpsConfigSection(
            key=section.key,
            title=section.title,
            status=section.status,
            fields=[
                OpsConfigField(
                    name=field.name,
                    env_var=field.env_var,
                    configured=field.configured,
                    required=field.required,
                    value=field.value,
                    description=field.description,
                )
                for field in section.fields
            ],
        )
        for section in get_config_status(settings)
    ]
    missing = get_missing_required_settings(settings)
    warnings: list[str] = []
    if settings.maps_live_trading_enabled and settings.maps_broker_mode == "mock":
        warnings.append("MAPS_LIVE_TRADING_ENABLED is true, but MAPS_BROKER_MODE is mock.")
    if settings.kis_real_trading and not settings.maps_live_trading_enabled:
        warnings.append("KIS_REAL_TRADING is true, but MAPS_LIVE_TRADING_ENABLED is false.")

    return OpsConfigResponse(
        ready=not missing and not warnings,
        broker_mode=settings.maps_broker_mode,
        live_trading_enabled=settings.maps_live_trading_enabled,
        data_provider=settings.maps_data_provider,
        ai_scoring_mode=settings.maps_ai_scoring_mode,
        missing_required=missing,
        warnings=warnings,
        sections=sections,
    )


@router.post("/ai-scoring-mode", response_model=AIScoringModeResponse)
def set_ai_scoring_mode(update: AIScoringModeUpdate) -> AIScoringModeResponse:
    """Persist the mode and update the current single-worker scheduler immediately."""
    settings = get_settings()
    previous = settings.maps_ai_scoring_mode
    _set_env_value(_ENV_FILE, "MAPS_AI_SCORING_MODE", update.mode)
    settings.maps_ai_scoring_mode = update.mode
    return AIScoringModeResponse(mode=update.mode, previous_mode=previous)


@router.get("/broker-health", response_model=BrokerHealthResponse)
def get_broker_health() -> BrokerHealthResponse:
    """브로커 연결을 진단한다 — 토큰 발급 + 잔고 조회 성공 여부 (주문 미제출).

    KIS 모의투자 활성화 전, 계좌·키 유효성과 paper/real 모드를 안전하게 확인하기 위한
    헬스체크. 어떤 주문도 제출하지 않으며, 실패해도 예외 대신 ok=false로 반환한다.
    """
    from maps.execution.broker_adapter import get_broker

    settings = get_settings()
    account_masked = mask_config_value(settings.kis_account_no, secret=True)
    try:
        policy = effective_policy(settings)
        settings = settings.model_copy(update={"maps_live_trading_enabled": policy["enabled"]})
        broker = get_broker(settings.maps_broker_mode)
        balance = broker.get_account_balance()
        return BrokerHealthResponse(
            ok=True,
            broker_mode=settings.maps_broker_mode,
            trading_mode=describe_trading_mode(settings),
            account_masked=account_masked,
            cash=balance.cash,
            positions_value=balance.positions_value,
            total_assets=balance.total_value,
        )
    except Exception as exc:  # noqa: BLE001 — 진단용: 어떤 실패도 ok=false로 보고
        return BrokerHealthResponse(
            ok=False,
            broker_mode=settings.maps_broker_mode,
            trading_mode=describe_trading_mode(settings),
            account_masked=account_masked,
            error=str(exc),
        )
