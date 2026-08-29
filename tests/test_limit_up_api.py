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
            "mode": "automatic",
            "attempts": 1,
            "pattern_failures": 0,
            "entry_halted": False,
            "halted_reasons": [],
            "manual_lock": False,
            "unknown_positions": [],
            "sessions": {},
        }

    def emergency_off(self) -> None:
        """Record one immediate entry shutdown."""
        self.off_calls += 1


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
