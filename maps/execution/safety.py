"""Execution mode, account identity and single-host writer ownership."""
from __future__ import annotations

import atexit
import datetime as dt
import hashlib
import logging
import os
import threading
import time
from contextlib import contextmanager
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from functools import wraps

from maps.common.exceptions import ExecutionBlockedError
from maps.common.settings import get_settings

_locks: dict[str, threading.RLock] = {}
_files: dict[str, object] = {}
_guard = threading.RLock()
_owner_pid = os.getpid()
_lock_timings: dict[str, dict] = {}
logger = logging.getLogger(__name__)


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def execution_environment(settings=None) -> str:
    settings = settings or get_settings()
    if settings.maps_broker_mode == "kis":
        return "kis_live" if settings.kis_real_trading else "kis_paper"
    return settings.maps_broker_mode


def account_key(settings=None) -> str:
    settings = settings or get_settings()
    identity = settings.kis_account_no.strip()
    if len(identity) == 8:
        identity += "-01"
    return hashlib.sha256(f"{execution_environment(settings)}:{identity}".encode()).hexdigest()


def require_execution_enabled(settings=None, *, source=None, check_scope=False) -> None:
    settings = settings or get_settings()
    latest = get_settings()
    if settings.maps_dry_run or latest.maps_dry_run:
        raise ExecutionBlockedError("dry_run")
    from maps.execution.runtime_policy import require_policy
    require_policy(settings, source=source, check_scope=check_scope)
    if (settings.maps_broker_mode in ("kis", "kiwoom") and settings.kis_real_trading
            and not settings.maps_confirm_real_trading):
        raise ExecutionBlockedError("real_trading_unconfirmed")
    if settings.maps_broker_mode == "kiwoom":
        raise ExecutionBlockedError("broker_execution_unsupported")


@dataclass(frozen=True)
class ExecutionContext:
    event_key: str
    source: str = "catalog"
    source_id: int | None = None
    valid_until: dt.datetime | None = None
    actor: str = ""
    is_admin: bool = False
    instance_id: str = ""
    sell_consent: bool = False
    strategy_version: str | None = None
    parameters: dict | None = None
    decision_at: dt.datetime | None = None
    data_as_of: dt.datetime | None = None
    # Transient process-local validity, never serialized into an order request.
    quote_is_current: Callable[[], bool] | None = field(default=None, repr=False, compare=False)


def require_quote_current(is_current: Callable[[], bool] | None) -> None:
    """Reject a quote invalidated by feed reset/failure across blocking work."""
    if is_current is not None and not is_current():
        raise ExecutionBlockedError("quote_invalidated")


def broker_execution_boundary(fn):
    """Serialize even direct broker sends with policy writes and verify source."""
    @wraps(fn)
    def guarded(self, *args, **kwargs):
        settings = getattr(self, "_settings", None) or get_settings()
        with account_execution_lock(account_key(settings)):
            require_execution_enabled(settings, check_scope=True)
            return fn(self, *args, **kwargs)
    return guarded


def _own_process(key: str) -> None:
    """Lock files are never unlinked: unlinking a locked inode breaks exclusion."""
    global _owner_pid
    if _owner_pid != os.getpid():
        release_process_locks()
        _owner_pid = os.getpid()
    root = Path(get_settings().maps_execution_lock_dir)
    if not root.is_absolute():
        root = Path(__file__).resolve().parents[2] / root
    root = root.resolve()
    if key in _files:
        if Path(_files[key].name).parent != root:
            raise ExecutionBlockedError("execution_lock_directory_changed_restart_required")
        return
    root.mkdir(parents=True, exist_ok=True)
    handle = (root / f"{key}.lock").open("a+b")
    try:
        if os.name == "nt":
            import msvcrt
            if handle.seek(0, 2) == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, IOError) as exc:
        handle.close()
        raise ExecutionBlockedError("account_owned_by_another_process") from exc
    _files[key] = handle


def claim_account_writer(key: str) -> None:
    """Claim process ownership without waiting for in-process broker execution."""
    with _guard:
        _own_process(key)


@contextmanager
def account_execution_lock(key: str):
    """Retain writer exclusion and report bounded, account-anonymous contention."""
    started = time.monotonic()
    # ponytail: one host, one writer/account; use DB fencing before multi-host deployment.
    with _guard:
        _own_process(key)
        lock = _locks.setdefault(key, threading.RLock())
    with lock:
        acquired = time.monotonic()
        try:
            yield
        finally:
            finished = time.monotonic()
            # Nested manager locks remain reentrant; context durations overlap
            # and must not be summed or mistaken for source snapshot freshness.
            with _guard:
                stats = _lock_timings.setdefault(key, dict(at=started, count=0, wait=0., hold=0.))
                stats["count"] += 1
                stats["wait"] = max(stats["wait"], acquired - started)
                stats["hold"] = max(stats["hold"], finished - acquired)
                if finished - stats["at"] >= 60:
                    if max(stats["wait"], stats["hold"]) >= 1:
                        logger.warning("Account execution latency samples=%d lock_wait_max_seconds=%.3f lock_hold_max_seconds=%.3f",
                                       stats["count"], stats["wait"], stats["hold"])
                    stats.update(at=finished, count=0, wait=0., hold=0.)


@atexit.register
def release_process_locks() -> None:
    for handle in _files.values():
        handle.close()
    _files.clear()
