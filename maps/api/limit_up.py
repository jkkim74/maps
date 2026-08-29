"""Admin API for upper-limit V1 runtime inspection and emergency shutdown."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from maps.api.schemas import (
    LimitUpSettingsUpdate,
    LimitUpStatusResponse,
)
from maps.common.settings import get_settings
from maps.limit_up import bootstrap
from maps.limit_up.service import LimitUpMode, automatic_mode_blocked_reason


router = APIRouter(prefix="/api/v1/limit-up", tags=["Limit Up V1"])

_STOPPED = {
    "mode": "off",
    "attempts": 0,
    "pattern_failures": 0,
    "daily_pnl": 0.0,
    "entry_halted": True,
    "halted_reasons": ["engine_not_running"],
    "manual_lock": False,
    "unknown_positions": [],
    "sessions": {},
}


@router.get("/status", response_model=LimitUpStatusResponse)
def get_limit_up_status() -> LimitUpStatusResponse:
    """Return the live V1 state machine snapshot.

    Reports a halted engine rather than an error when nothing is running: the
    screen asking this is an operations view, and "not running" is an answer.
    """
    runtime = bootstrap.get_runtime()
    if runtime is None:
        return LimitUpStatusResponse(**_STOPPED)
    return LimitUpStatusResponse(**{**_STOPPED, **runtime.service.status()})


@router.post("/emergency-off", response_model=LimitUpStatusResponse)
def emergency_off() -> LimitUpStatusResponse:
    """Latch the engine OFF immediately, blocking new entries.

    Blocks *entries* only. Exiting an existing position stays available — the
    kill switch never strands a holding without a way out.
    """
    runtime = bootstrap.get_runtime()
    if runtime is not None:
        runtime.emergency_off()
    return LimitUpStatusResponse(**_STOPPED)


@router.put("/settings", response_model=LimitUpStatusResponse)
def update_limit_up_settings(payload: LimitUpSettingsUpdate) -> LimitUpStatusResponse:
    """Apply admin-changeable V1 runtime settings.

    The schema rejects anything that would weaken the hard liquidity floor, so no
    admin action can loosen it. Mode changes take effect on the running engine
    when one exists; persisting them is the operator's ``.env`` job.

    Switching to ``automatic`` is refused unless the account-wide live-trading
    switches allow real orders — this endpoint must not become a way around
    ``MAPS_LIVE_TRADING_ENABLED``.

    Raises:
        HTTPException: 409 when automatic is blocked by a safety switch.
    """
    if payload.mode == LimitUpMode.AUTOMATIC.value:
        blocked = automatic_mode_blocked_reason(get_settings())
        if blocked is not None:
            raise HTTPException(
                status_code=409,
                detail=f"automatic mode blocked by safety switch: {blocked}",
            )
    runtime = bootstrap.get_runtime()
    if runtime is None:
        return LimitUpStatusResponse(**{**_STOPPED, "mode": payload.mode})
    runtime.apply_settings(
        mode=payload.mode, min_turnover_krw=payload.min_turnover_krw
    )
    return LimitUpStatusResponse(**{**_STOPPED, **runtime.service.status()})
