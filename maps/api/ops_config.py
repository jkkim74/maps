"""Operational configuration readiness API."""

from __future__ import annotations

from fastapi import APIRouter

from maps.api.schemas import (
    OpsConfigField,
    OpsConfigResponse,
    OpsConfigSection,
)
from maps.common.settings import get_config_status, get_missing_required_settings, get_settings

router = APIRouter(prefix="/api/v1/ops/config", tags=["Ops Config"])


@router.get("", response_model=OpsConfigResponse)
def get_ops_config() -> OpsConfigResponse:
    """Return masked external-integration configuration status."""
    settings = get_settings()
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
        missing_required=missing,
        warnings=warnings,
        sections=sections,
    )
