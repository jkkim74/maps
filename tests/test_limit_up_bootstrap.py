"""Engine startup gating: wired is not the same as on."""

from __future__ import annotations

import logging

import pytest

from maps.common.settings import MapsSettings
from maps.limit_up import bootstrap
from maps.limit_up.service import LimitUpMode


@pytest.fixture(autouse=True)
def _clear_runtime():
    """Never let one test's runtime handle leak into another."""
    bootstrap.set_runtime(None)
    yield
    bootstrap.set_runtime(None)


async def test_engine_stays_off_by_default() -> None:
    """Wiring the engine must not start real-time scanning on its own."""
    settings = MapsSettings(maps_broker_mode="kis")

    assert settings.maps_limit_up_enabled is False
    await bootstrap.start_limit_up_if_enabled(settings)

    assert bootstrap.get_runtime() is None


async def test_engine_refuses_a_broker_without_a_realtime_feed(caplog) -> None:
    """Starting on mock would leave a live engine that silently never triggers."""
    settings = MapsSettings(maps_limit_up_enabled=True, maps_broker_mode="mock")

    with caplog.at_level(logging.ERROR):
        await bootstrap.start_limit_up_if_enabled(settings)

    assert bootstrap.get_runtime() is None
    assert "기동 거부" in caplog.text


async def test_startup_failure_never_takes_down_the_api(monkeypatch, caplog) -> None:
    """The admin endpoints that latch the engine OFF must stay reachable."""
    settings = MapsSettings(maps_limit_up_enabled=True, maps_broker_mode="kis")

    def _boom(_settings):
        raise RuntimeError("KIS credentials missing")

    monkeypatch.setattr(bootstrap, "build_runtime", _boom)

    with caplog.at_level(logging.ERROR):
        await bootstrap.start_limit_up_if_enabled(settings)

    assert bootstrap.get_runtime() is None
    assert "기동 실패" in caplog.text


def test_recommend_only_still_builds_a_command_worker(monkeypatch) -> None:
    """A missing worker would make a later switch to automatic a silent no-op.

    Every order path is guarded by ``mode is AUTOMATIC and worker is not None``,
    so without the worker the engine would look enabled and place nothing.
    """
    monkeypatch.setattr(
        "maps.limit_up.bootstrap.get_broker", lambda mode: _StubBroker()
    )
    settings = MapsSettings(
        maps_limit_up_enabled=True,
        maps_broker_mode="kis",
        maps_limit_up_mode="recommend_only",
    )

    runtime = bootstrap.build_runtime(settings)

    assert runtime.service.mode is LimitUpMode.RECOMMEND_ONLY
    assert runtime.service.worker is not None


class _StubBroker:
    """Minimal stand-in so assembly can be checked without KIS credentials."""

    def get_positions(self) -> dict[str, int]:
        """Return no holdings."""
        return {}
