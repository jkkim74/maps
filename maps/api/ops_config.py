"""Operational configuration readiness API."""

from __future__ import annotations

from fastapi import APIRouter

from maps.api.schemas import (
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
