"""US background work is opt-in and each job owns its database session."""
from types import SimpleNamespace
import threading

import pytest

from maps.common.exceptions import ExecutionBlockedError


def test_disabled_runtime_does_not_start_or_contact_broker(monkeypatch):
    from maps.ops import us_trading
    monkeypatch.setattr(us_trading, "get_settings", lambda: SimpleNamespace(maps_us_enabled=False))
    monkeypatch.setattr(us_trading, "_has_unresolved_orders", lambda settings: False)
    us_trading.start_us_trading()
    assert us_trading._scheduler is None
    with pytest.raises(ExecutionBlockedError):
        us_trading.request_sync("admin")


def test_disabled_execution_retains_sync_for_unresolved_orders(monkeypatch):
    from maps.ops import us_trading
    monkeypatch.setattr(us_trading, "get_settings", lambda: SimpleNamespace(maps_us_enabled=False))
    monkeypatch.setattr(us_trading, "_has_unresolved_orders", lambda settings: True)
    finished = threading.Event()
    def worker(actor):
        us_trading._sync_lock.release()
        finished.set()
    monkeypatch.setattr(us_trading, "_run_sync", worker)
    assert us_trading.request_sync("admin")["status"] == "queued"
    assert finished.wait(2)


def test_overlapping_sync_is_not_queued_twice(monkeypatch):
    from maps.ops import us_trading
    monkeypatch.setattr(us_trading, "get_settings", lambda: SimpleNamespace(maps_us_enabled=True))
    assert us_trading._sync_lock.acquire(blocking=False)
    try:
        result = us_trading.request_sync("admin")
        assert result["status"] == "already_running"
    finally:
        us_trading._sync_lock.release()


def test_background_failure_closes_session_and_releases_lock(monkeypatch):
    from maps.ops import us_trading
    closed = []
    class Session:
        def __enter__(self): return self
        def __exit__(self, *args): closed.append(True)
    monkeypatch.setattr(us_trading, "SessionLocal", Session)
    def fail(db):
        raise ExecutionBlockedError("test_failure")
    monkeypatch.setattr(us_trading, "make_service", fail)
    assert us_trading._sync_lock.acquire(blocking=False)
    us_trading._run_sync("test")
    assert closed == [True]
    assert us_trading._sync_lock.acquire(blocking=False)
    us_trading._sync_lock.release()
