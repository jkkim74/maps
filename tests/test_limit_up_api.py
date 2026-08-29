"""Admin API tests for upper-limit V1 runtime controls."""

from __future__ import annotations

from fastapi.testclient import TestClient

import main
from maps.limit_up import bootstrap


class FakeRuntime:
    """Small runtime probe for API state-change assertions."""

    def __init__(self) -> None:
        """Start with an automatic operational snapshot."""
        self.off_calls = 0
        self.service = self

    def status(self) -> dict:
        """Return a service-compatible status payload."""
        return {
            "mode": self.mode,
            "attempts": 1,
            "pattern_failures": 0,
            "entry_halted": False,
            "halted_reasons": [],
            "manual_lock": False,
            "unknown_positions": [],
            "sessions": {},
        }

    def emergency_off(self) -> None:
        """Record one immediate entry shutdown and reflect it in status."""
        self.off_calls += 1
        self.mode = "off"

    mode = "automatic"


def test_limit_up_status_and_emergency_off_are_admin_controls(monkeypatch) -> None:
    """Operators can inspect the FSM and latch OFF without killing exits."""
    runtime = FakeRuntime()
    monkeypatch.setattr(bootstrap, "_runtime", runtime)
    client = TestClient(main.app)

    status = client.get("/api/v1/limit-up/status")
    stopped = client.post("/api/v1/limit-up/emergency-off")

    assert status.status_code == 200
    assert status.json()["attempts"] == 1
    assert stopped.status_code == 200
    assert stopped.json()["mode"] == "off"
    assert runtime.off_calls == 1


def test_limit_up_settings_reject_turnover_below_absolute_floor() -> None:
    """No admin action may weaken the hard 50-billion-KRW gate."""
    response = TestClient(main.app).put(
        "/api/v1/limit-up/settings",
        json={"mode": "recommend_only", "min_turnover_krw": 49_999_999_999},
    )

    assert response.status_code == 422


def test_admin_api_cannot_switch_to_automatic_past_the_live_switch(monkeypatch) -> None:
    """The startup gate alone is not enough — mode can change after startup."""
    runtime = FakeRuntime()
    monkeypatch.setattr(bootstrap, "_runtime", runtime)
    monkeypatch.setattr(
        "maps.api.limit_up.automatic_mode_blocked_reason",
        lambda settings: "live_trading_disabled",
    )

    response = TestClient(main.app).put(
        "/api/v1/limit-up/settings",
        json={"mode": "automatic", "min_turnover_krw": 50_000_000_000},
    )

    assert response.status_code == 409
    assert "live_trading_disabled" in response.json()["detail"]


def test_admin_api_still_allows_recommend_only(monkeypatch) -> None:
    """The gate must only stand in front of automatic."""
    runtime = FakeRuntime()
    runtime.apply_settings = lambda **kwargs: None
    monkeypatch.setattr(bootstrap, "_runtime", runtime)

    response = TestClient(main.app).put(
        "/api/v1/limit-up/settings",
        json={"mode": "recommend_only", "min_turnover_krw": 50_000_000_000},
    )

    assert response.status_code == 200
