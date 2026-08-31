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


def test_emergency_off_reports_when_runtime_loop_is_absent(monkeypatch) -> None:
    runtime = FakeRuntime()
    runtime.emergency_off = lambda: (_ for _ in ()).throw(RuntimeError("loop absent"))
    monkeypatch.setattr(bootstrap, "_runtime", runtime)

    response = TestClient(main.app).post("/api/v1/limit-up/emergency-off")

    assert response.status_code == 503
    assert "not queued" in response.json()["detail"]


def test_emergency_off_timeout_truthfully_reports_queued(monkeypatch) -> None:
    runtime = FakeRuntime()
    runtime.emergency_off = lambda: (_ for _ in ()).throw(TimeoutError("busy"))
    monkeypatch.setattr(bootstrap, "_runtime", runtime)

    response = TestClient(main.app).post("/api/v1/limit-up/emergency-off")

    assert response.status_code == 503
    assert "queued and will apply" in response.json()["detail"]


def _sessions_store(tmp_path, monkeypatch):
    """`/limit-up/sessions` 가 읽을 임시 원장을 만들고 `SessionLocal` 을 돌린다."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from maps.common import db as db_module
    from maps.common.db import Base

    engine = create_engine(
        f"sqlite:///{(tmp_path / 'limit_up.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(db_module, "SessionLocal", factory)
    return factory


def test_sessions_endpoint_survives_a_restart_that_empties_status(
    tmp_path, monkeypatch
) -> None:
    """인메모리 상태가 비어도 그날 무슨 일이 있었는지는 남아 있어야 한다.

    `/status` 만 있으면 장 마감 뒤 재시작한 운영자가 "엔진이 오늘 아무것도 안 했다"
    와 "엔진이 방금 재시작했다" 를 구별할 수 없다.
    """
    import datetime as dt

    from maps.limit_up.domain import LimitUpState
    from maps.limit_up.repository import LimitUpRepository

    factory = _sessions_store(tmp_path, monkeypatch)
    monkeypatch.setattr(bootstrap, "_runtime", None)  # 엔진 정지 상태를 흉내낸다
    db = factory()
    repo = LimitUpRepository(db)
    session = repo.create_or_get_session(
        ref_date=dt.date(2026, 8, 31),
        ticker="005930",
        market="KOSPI",
        upper_limit_price=100_000,
        trigger_price=99_700,
        execution_mode="recommend_only",
    )
    repo.upsert_leg(session, name="S", price=100_000, quantity=12, status="recommended")
    repo.transition(session, state=LimitUpState.NET_OPEN, action="fire_net")
    db.commit()
    db.close()

    client = TestClient(main.app)
    status = client.get("/api/v1/limit-up/status").json()
    day = client.get("/api/v1/limit-up/sessions?ref_date=2026-08-31").json()

    assert status["sessions"] == {}
    assert [row["ticker"] for row in day["sessions"]] == ["005930"]
    assert day["sessions"][0]["legs"][0]["status"] == "recommended"
    assert [event["action"] for event in day["sessions"][0]["events"]] == ["fire_net"]


def test_limit_up_screen_renders(tmp_path, monkeypatch) -> None:
    """화면이 템플릿 오류로 죽지 않는지 — 라우트와 nav 배선까지 함께 태운다."""
    _sessions_store(tmp_path, monkeypatch)

    response = TestClient(main.app).get("/limit-up")

    assert response.status_code == 200
    assert "상한가 V1" in response.text
