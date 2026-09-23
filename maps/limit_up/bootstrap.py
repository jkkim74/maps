"""Process-wide startup and handle for the upper-limit V1 runtime.

Starting the engine means a live intraday WebSocket, continuous scanning, and —
in ``automatic`` mode — real orders. So it is gated behind an explicit
``MAPS_LIMIT_UP_ENABLED`` switch, off by default, the same way the operational
scheduler is. Being wired is not the same as being on.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from enum import Enum
from typing import Any

from maps.common.db import SessionLocal
from maps.common.settings import MapsSettings
from maps.execution.broker_adapter import get_broker
from maps.execution.order_manager import OrderManager
from maps.limit_up import notify
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

# 기동 실패 재시도 백오프. 첫 재시도 1분 — 06:04 자동 업데이트 때 PG 재시작은 3초였다.
_RETRY_INITIAL_SECONDS = 60.0
_RETRY_MAX_SECONDS = 900.0


class StartOutcome(str, Enum):
    """엔진 기동 시도 결과. 재시도는 ``FAILED`` 에만 한다."""

    DISABLED = "disabled"  # MAPS_LIMIT_UP_ENABLED=false
    REFUSED = "refused"    # 설정이 기동을 거부 — 재시도해도 같다
    STARTED = "started"
    FAILED = "failed"      # 예외 — 일시적일 수 있다(DB 재시작 등)


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


async def start_limit_up_if_enabled(settings: MapsSettings) -> StartOutcome:
    """Start the V1 engine when it is explicitly enabled and able to run.

    Refuses rather than degrades. The engine needs a live KIS feed; on any other
    broker there is no real-time tape, and starting anyway would leave a running
    engine that silently never triggers.

    A startup failure takes down the engine, never the API server — the admin
    endpoints that inspect and latch it off must stay reachable.

    Args:
        settings: Resolved application settings.

    Returns:
        What happened. Only ``FAILED`` is worth retrying.
    """
    if not settings.maps_limit_up_enabled:
        return StartOutcome.DISABLED
    if settings.maps_broker_mode != "kis":
        logger.error(
            "상한가 V1 기동 거부 — 실시간 시세가 없는 브로커 모드(%s). "
            "MAPS_BROKER_MODE=kis 가 필요하다.",
            settings.maps_broker_mode,
        )
        return StartOutcome.REFUSED
    if settings.maps_limit_up_mode == LimitUpMode.AUTOMATIC.value:
        blocked = automatic_mode_blocked_reason(settings)
        if blocked is not None:
            logger.error(
                "상한가 V1 기동 거부 — automatic 요청이 실주문 안전 스위치를 통과하지 "
                "못했다(%s). recommend_only 로 조용히 낮추지 않는다. "
                "MAPS_LIMIT_UP_MODE 를 직접 고칠 것.",
                blocked,
            )
            return StartOutcome.REFUSED
    try:
        runtime = build_runtime(settings)
        await runtime.start()
    except Exception:
        logger.exception("상한가 V1 기동 실패 — 엔진 없이 서버만 계속한다")
        return StartOutcome.FAILED
    set_runtime(runtime)
    logger.warning(
        "=== 상한가 V1 기동: mode=%s (automatic 이 아니면 주문은 나가지 않는다) ===",
        settings.maps_limit_up_mode,
    )
    return StartOutcome.STARTED


async def retry_limit_up_start(
    settings: MapsSettings,
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """기동이 실패한 뒤 엔진이 뜰 때까지 백오프로 다시 시도한다.

    2026-09-23 06:04 자동 업데이트가 PostgreSQL 과 maps 를 함께 재시작했고, 엔진의 복구
    조회가 재시작 중인 DB 에 걸려 기동이 실패했다. 재시도가 없어 **09:21 까지 엔진 없이**
    장이 열렸다. 설정 거부(``REFUSED``)는 다시 해도 같으므로 멈춘다. 누군가 먼저 띄웠으면
    (``get_runtime()``) 그대로 끝낸다. 호출부는 첫 실패 알림을 이미 보냈다고 가정한다.
    """
    delay = _RETRY_INITIAL_SECONDS
    attempt = 1
    while True:
        await sleep(delay)
        if get_runtime() is not None:
            return
        attempt += 1
        outcome = await start_limit_up_if_enabled(settings)
        if outcome is StartOutcome.STARTED:
            notify.push(f"✅ <b>상한가 엔진 복구</b> — 기동 재시도 {attempt}회차에 성공")
            return
        if outcome is not StartOutcome.FAILED:
            return
        delay = min(delay * 2, _RETRY_MAX_SECONDS)


def alert_start_failed() -> None:
    """첫 기동 실패를 알린다 — 로그에만 남으면 장이 열려도 아무도 모른다."""
    notify.push(
        "🚨 <b>상한가 엔진 기동 실패</b> — 서버는 계속 동작, "
        f"{_RETRY_INITIAL_SECONDS:.0f}초부터 백오프로 재시도한다. 원인은 서버 로그 확인"
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
