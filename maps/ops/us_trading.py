"""Opt-in US paper synchronization; every worker owns its database session."""
from __future__ import annotations

import logging
import threading

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import Session

from maps.common.db import SessionLocal
from maps.common.exceptions import ExecutionBlockedError
from maps.common.settings import get_settings

logger = logging.getLogger(__name__)
_sync_lock = threading.Lock()
_scheduler: BackgroundScheduler | None = None


def sync_running() -> bool:
    """Expose the actual worker gate rather than a request-local service flag."""
    return _sync_lock.locked()


def _has_unresolved_orders(settings) -> bool:
    """Execution disable must not abandon existing broker uncertainty."""
    from sqlalchemy import select
    from maps.common.models import USOrderLedger
    from maps.execution.us_contracts import us_account_key
    with SessionLocal() as db:
        return db.scalar(select(USOrderLedger.id).where(
            USOrderLedger.account_key == us_account_key(settings),
            USOrderLedger.status.in_(("unknown", "pending", "partially_filled", "cancel_requested")),
        ).limit(1)) is not None


class _LazyBroker:
    """Read-only screens do not initialize credentials, transport or broker state."""

    def __init__(self, db: Session, settings) -> None:
        self.db, self.settings, self._broker = db, settings, None

    def __getattr__(self, name: str):
        if self._broker is None:
            if self.settings.maps_us_broker_mode == "mock":
                from maps.execution.us_mock_broker import USMockBroker
                self._broker = USMockBroker(self.db, self.settings)
            elif self.settings.maps_us_broker_mode == "kis_paper":
                from maps.execution.kis_us_adapter import KISUSAdapter
                self._broker = KISUSAdapter(self.settings)
            else:
                raise ExecutionBlockedError("us_live_environment_forbidden")
        return getattr(self._broker, name)

    def close(self) -> None:
        """Release the independent simulator Session or HTTP pool after each operation."""
        if self._broker is None:
            return
        if self.settings.maps_us_broker_mode == "mock":
            self._broker.db.close()
        else:
            self._broker._http.close()


def make_service(db: Session):
    """Use the same service for administrator requests and scheduled reconciliation."""
    from maps.execution.us_service import USExecutionService
    settings = get_settings()
    return USExecutionService(db, _LazyBroker(db, settings), settings=settings)


def close_service(service) -> None:
    """Do not initialize a lazy broker merely to clean it up."""
    if isinstance(service.broker, _LazyBroker):
        service.broker.close()


def _run_sync(actor: str) -> None:
    """Always release the in-process gate; execution also holds its account lock."""
    try:
        with SessionLocal() as db:
            service = make_service(db)
            try:
                service.sync()
            finally:
                close_service(service)
    except Exception as exc:
        # Do not print raw transport errors: they can contain credentials or payloads.
        logger.error("US synchronization failed (%s), actor=%s", type(exc).__name__, actor)
    finally:
        _sync_lock.release()


def request_sync(actor: str = "scheduler") -> dict:
    """Start at most one sync worker; no request-bound Session crosses threads."""
    settings = get_settings()
    if not settings.maps_us_enabled and not _has_unresolved_orders(settings):
        raise ExecutionBlockedError("us_trading_disabled")
    if not _sync_lock.acquire(blocking=False):
        return {"status": "already_running"}
    try:
        threading.Thread(target=_run_sync, args=(actor,), name="maps-us-sync", daemon=True).start()
    except Exception:
        _sync_lock.release()
        raise
    return {"status": "queued"}


def start_us_trading() -> None:
    """Synchronize on startup and periodically, including unresolved overnight orders."""
    global _scheduler
    settings = get_settings()
    if _scheduler is not None or (not settings.maps_us_enabled and not _has_unresolved_orders(settings)):
        return
    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(request_sync, "interval", seconds=settings.maps_us_sync_seconds,
                       id="us_paper_sync", max_instances=1, coalesce=True)
    _scheduler.start()
    request_sync("startup")


def shutdown_us_trading() -> None:
    """Stop future scheduling without cancelling or liquidating any broker order."""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
