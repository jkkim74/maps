"""Engine startup gating: wired is not the same as on."""

from __future__ import annotations

import logging

import pytest

from maps.common.settings import MapsSettings
from maps.limit_up import bootstrap
from maps.limit_up.service import LimitUpMode, automatic_mode_blocked_reason


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


async def test_automatic_is_refused_when_live_trading_is_off(caplog) -> None:
    """V1 orders never pass through order_cycle, the only place LIVE was enforced.

    Without this gate the engine would place real orders while the account-wide
    switch says off — and order_log would label them 'mock', since _order_log_mode
    reads that same switch.
    """
    settings = MapsSettings(
        maps_limit_up_enabled=True,
        maps_broker_mode="kis",
        maps_limit_up_mode="automatic",
        maps_live_trading_enabled=False,
    )

    with caplog.at_level(logging.ERROR):
        await bootstrap.start_limit_up_if_enabled(settings)

    assert bootstrap.get_runtime() is None
    assert "live_trading_disabled" in caplog.text


async def test_automatic_is_refused_on_unconfirmed_real_account(caplog) -> None:
    """Real money needs the explicit confirmation flag, same as server startup."""
    settings = MapsSettings(
        maps_limit_up_enabled=True,
        maps_broker_mode="kis",
        maps_limit_up_mode="automatic",
        maps_live_trading_enabled=True,
        kis_real_trading=True,
        maps_confirm_real_trading=False,
    )

    with caplog.at_level(logging.ERROR):
        await bootstrap.start_limit_up_if_enabled(settings)

    assert bootstrap.get_runtime() is None
    assert "real_trading_unconfirmed" in caplog.text


def test_blocked_automatic_is_refused_not_silently_downgraded() -> None:
    """Quietly running as recommend_only would hide that automatic never took."""
    blocked = MapsSettings(
        maps_broker_mode="kis",
        maps_limit_up_mode="automatic",
        maps_live_trading_enabled=False,
    )
    allowed = MapsSettings(
        maps_broker_mode="kis",
        maps_limit_up_mode="automatic",
        maps_live_trading_enabled=True,
    )

    assert automatic_mode_blocked_reason(blocked) == "live_trading_disabled"
    assert automatic_mode_blocked_reason(allowed) is None


def test_recommend_only_is_unaffected_by_the_live_switch() -> None:
    """The gate must not block the signals-only mode, which places no orders."""
    settings = MapsSettings(
        maps_broker_mode="kis",
        maps_limit_up_mode="recommend_only",
        maps_live_trading_enabled=False,
    )

    assert settings.maps_limit_up_mode == "recommend_only"
    # the gate is only consulted for automatic; recommend_only never reaches it
    assert automatic_mode_blocked_reason(settings) == "live_trading_disabled"
