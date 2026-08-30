"""Process-wide startup and handle for the upper-limit V1 runtime.

Starting the engine means a live intraday WebSocket, continuous scanning, and —
in ``automatic`` mode — real orders. So it is gated behind an explicit
``MAPS_LIMIT_UP_ENABLED`` switch, off by default, the same way the operational
scheduler is. Being wired is not the same as being on.
"""

from __future__ import annotations

import logging
from typing import Any

from maps.common.db import SessionLocal
from maps.common.settings import MapsSettings
from maps.execution.broker_adapter import get_broker
from maps.execution.order_manager import OrderManager
from maps.limit_up.domain import LimitUpConfig
from maps.limit_up.repository import LimitUpRepository
from maps.limit_up.runtime import KISIntradayRuntime
from maps.limit_up.service import (
    LimitUpMode,
    LimitUpService,
    automatic_mode_blocked_reason,
)
from maps.limit_up.worker import LimitUpCommandWorker
from maps.risk.manager import RiskConfig, RiskManager


logger = logging.getLogger(__name__)

_runtime: Any | None = None


def get_runtime() -> Any | None:
    """Return the running V1 runtime, or ``None`` when the engine is not up."""
    return _runtime


def set_runtime(runtime: Any | None) -> None:
    """Register (or clear) the process runtime handle.

    Args:
        runtime: The started runtime, or ``None`` to clear it on shutdown.
    """
    global _runtime
    _runtime = runtime


def build_runtime(settings: MapsSettings) -> KISIntradayRuntime:
    """Assemble the V1 runtime and every collaborator it needs.

    The command worker is built **regardless of mode**. In ``recommend_only``
    every order path is guarded by ``can_place_exit_for()`` (the session's ``execution_mode``),
    so a present worker submits nothing — but a *missing* one would make a later
    switch to ``automatic`` fall through to the simulation branch and place no
    orders at all, with no error. The engine would look on and do nothing.

    Args:
        settings: Resolved application settings.

    Returns:
        A runtime ready for ``await start()``.
    """
    db = SessionLocal()
    broker = get_broker(settings.maps_broker_mode)
    repository = LimitUpRepository(db)
    risk = RiskManager(
        broker=broker,
        db=db,
        config=RiskConfig(
            daily_loss_limit=settings.daily_loss_limit,
            position_size_limit=settings.max_single_exposure,
        ),
    )
    worker = LimitUpCommandWorker(OrderManager(broker=broker, risk=risk, db=db), broker, repository)
    service = LimitUpService(
        mode=LimitUpMode(settings.maps_limit_up_mode),
        config=LimitUpConfig(min_turnover_krw=settings.maps_limit_up_min_turnover_krw),
        repository=repository,
        worker=worker,
    )
    return KISIntradayRuntime(
        settings=settings, db=db, adapter=broker, service=service
    )


async def start_limit_up_if_enabled(settings: MapsSettings) -> None:
    """Start the V1 engine when it is explicitly enabled and able to run.

    Refuses rather than degrades. The engine needs a live KIS feed; on any other
    broker there is no real-time tape, and starting anyway would leave a running
    engine that silently never triggers.

    A startup failure takes down the engine, never the API server — the admin
    endpoints that inspect and latch it off must stay reachable.

    Args:
        settings: Resolved application settings.
    """
    if not settings.maps_limit_up_enabled:
        return
    if settings.maps_broker_mode != "kis":
        logger.error(
            "상한가 V1 기동 거부 — 실시간 시세가 없는 브로커 모드(%s). "
            "MAPS_BROKER_MODE=kis 가 필요하다.",
            settings.maps_broker_mode,
        )
        return
    if settings.maps_limit_up_mode == LimitUpMode.AUTOMATIC.value:
        blocked = automatic_mode_blocked_reason(settings)
        if blocked is not None:
            logger.error(
                "상한가 V1 기동 거부 — automatic 요청이 실주문 안전 스위치를 통과하지 "
                "못했다(%s). recommend_only 로 조용히 낮추지 않는다. "
                "MAPS_LIMIT_UP_MODE 를 직접 고칠 것.",
                blocked,
            )
            return
    try:
        runtime = build_runtime(settings)
        await runtime.start()
    except Exception:
        logger.exception("상한가 V1 기동 실패 — 엔진 없이 서버만 계속한다")
        return
    set_runtime(runtime)
    logger.warning(
        "=== 상한가 V1 기동: mode=%s (automatic 이 아니면 주문은 나가지 않는다) ===",
        settings.maps_limit_up_mode,
    )


async def shutdown_limit_up() -> None:
    """Stop the engine if one is running, leaving broker state recoverable."""
    runtime = get_runtime()
    if runtime is None:
        return
    try:
        await runtime.stop()
    except Exception:
        logger.exception("상한가 V1 종료 중 오류")
    finally:
        set_runtime(None)
