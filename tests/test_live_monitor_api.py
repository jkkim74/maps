"""Live Monitor API 회귀 테스트.

Kill Switch 상태 머신 전체 흐름을 TestClient로 자동 검증한다.

흐름:
  (없음) -> trigger -> approved -> deactivate
               │           │
            중복승인 409   중복해제 409
  release 이후 재승인 시도 409
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import maps.common.models  # noqa: F401 — 모델 등록
from maps.common.db import Base
from maps.common.models import KillSwitchLog


@pytest.fixture
def ctx():
    """TestClient + DB 팩토리.

    SQLite in-memory는 커넥션마다 별도 DB이므로 StaticPool로
    단일 커넥션을 강제해 create_all과 세션이 같은 DB를 공유한다.
    """
    from main import app
    from maps.api.deps import get_db

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app, raise_server_exceptions=True)
    yield client, factory

    app.dependency_overrides.pop(get_db, None)
    Base.metadata.drop_all(engine)
    engine.dispose()


def _seed_trigger(factory, strategy_id: str = "strat_test") -> None:
    """DB에 trigger 이벤트를 직접 삽입한다 (RiskManager 우회)."""
    db = factory()
    try:
        db.add(KillSwitchLog(
            strategy_id=strategy_id,
            event_type="trigger",
            reason="daily_loss_limit",
            value="일일 손실 2.00% > 한도 1.50%",
            new_entry_blocked=True,
            approved_by=None,
        ))
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 1. GET 기본 — 이벤트 없을 때
# ---------------------------------------------------------------------------

def test_get_empty(ctx) -> None:
    """이벤트 없을 때 카운트가 모두 0이다."""
    client, _ = ctx
    r = client.get("/api/v1/live-monitor")
    assert r.status_code == 200
    d = r.json()
    assert d["pending_approval_count"] == 0
    assert d["pending_release_count"] == 0
    assert d["pending_approvals"] == []
    assert d["pending_releases"] == []
    assert d["recent_events"] == []


# ---------------------------------------------------------------------------
# 2. trigger 삽입 후 GET
# ---------------------------------------------------------------------------

def test_get_after_trigger(ctx) -> None:
    """trigger 이벤트 삽입 후 pending_approval_count=1."""
    client, factory = ctx
    _seed_trigger(factory)

    r = client.get("/api/v1/live-monitor")
    assert r.status_code == 200
    d = r.json()
    assert d["pending_approval_count"] == 1
    assert d["pending_release_count"] == 0
    assert len(d["pending_approvals"]) == 1
    assert d["pending_approvals"][0]["event_type"] == "trigger"


# ---------------------------------------------------------------------------
# 3. approve-liquidation 정상 흐름
# ---------------------------------------------------------------------------

def test_approve_liquidation(ctx) -> None:
    """/approve-liquidation → approved 이벤트 삽입, 해제 대기 1."""
    client, factory = ctx
    _seed_trigger(factory)

    r = client.post("/api/v1/live-monitor/strat_test/approve-liquidation",
                    json={"approved_by": "admin"})
    assert r.status_code == 200
    assert r.json()["status"] == "liquidation_approved"

    monitor = client.get("/api/v1/live-monitor").json()
    assert monitor["pending_approval_count"] == 0
    assert monitor["pending_release_count"] == 1
    assert monitor["pending_releases"][0]["event_type"] == "approved"


# ---------------------------------------------------------------------------
# 4. 중복 approve → 409
# ---------------------------------------------------------------------------

def test_approve_duplicate_returns_409(ctx) -> None:
    """이미 approved 상태에서 재승인 → 409."""
    client, factory = ctx
    _seed_trigger(factory)

    client.post("/api/v1/live-monitor/strat_test/approve-liquidation",
                json={"approved_by": "admin"})

    r = client.post("/api/v1/live-monitor/strat_test/approve-liquidation",
                    json={"approved_by": "admin"})
    assert r.status_code == 409
    assert "이미 청산 승인된" in r.json()["detail"]


# ---------------------------------------------------------------------------
# 5. release 정상 흐름
# ---------------------------------------------------------------------------

def test_release(ctx) -> None:
    """/release → deactivate 이벤트 삽입, 모든 카운트 0."""
    client, factory = ctx
    _seed_trigger(factory)

    client.post("/api/v1/live-monitor/strat_test/approve-liquidation",
                json={"approved_by": "admin"})
    r = client.post("/api/v1/live-monitor/strat_test/release",
                    json={"approved_by": "admin"})
    assert r.status_code == 200
    assert r.json()["status"] == "released"

    monitor = client.get("/api/v1/live-monitor").json()
    assert monitor["pending_approval_count"] == 0
    assert monitor["pending_release_count"] == 0


# ---------------------------------------------------------------------------
# 6. 중복 release → 409
# ---------------------------------------------------------------------------

def test_release_duplicate_returns_409(ctx) -> None:
    """이미 deactivate 상태에서 재해제 → 409."""
    client, factory = ctx
    _seed_trigger(factory)

    client.post("/api/v1/live-monitor/strat_test/approve-liquidation",
                json={"approved_by": "admin"})
    client.post("/api/v1/live-monitor/strat_test/release",
                json={"approved_by": "admin"})

    r = client.post("/api/v1/live-monitor/strat_test/release",
                    json={"approved_by": "admin"})
    assert r.status_code == 409
    assert "이미 해제된" in r.json()["detail"]


# ---------------------------------------------------------------------------
# 7. release 후 approve 시도 → 409
# ---------------------------------------------------------------------------

def test_approve_after_release_returns_409(ctx) -> None:
    """release 이후 approve-liquidation 재시도 → 409."""
    client, factory = ctx
    _seed_trigger(factory)

    client.post("/api/v1/live-monitor/strat_test/approve-liquidation",
                json={"approved_by": "admin"})
    client.post("/api/v1/live-monitor/strat_test/release",
                json={"approved_by": "admin"})

    r = client.post("/api/v1/live-monitor/strat_test/approve-liquidation",
                    json={"approved_by": "admin"})
    assert r.status_code == 409
    assert "이미 해제된" in r.json()["detail"]


# ---------------------------------------------------------------------------
# 8. 긴급 해제 — approve 없이 바로 release
# ---------------------------------------------------------------------------

def test_emergency_release_without_approve(ctx) -> None:
    """approve-liquidation 없이 바로 /release → 정상 해제."""
    client, factory = ctx
    _seed_trigger(factory)

    r = client.post("/api/v1/live-monitor/strat_test/release",
                    json={"approved_by": "admin"})
    assert r.status_code == 200

    monitor = client.get("/api/v1/live-monitor").json()
    assert monitor["pending_approval_count"] == 0
    assert monitor["pending_release_count"] == 0


# ---------------------------------------------------------------------------
# 9. 존재하지 않는 전략 → 404
# ---------------------------------------------------------------------------

def test_approve_nonexistent_strategy_returns_404(ctx) -> None:
    """/approve-liquidation 대상 없음 → 404."""
    client, _ = ctx
    r = client.post("/api/v1/live-monitor/no_such_strat/approve-liquidation",
                    json={"approved_by": "admin"})
    assert r.status_code == 404


def test_release_nonexistent_strategy_returns_404(ctx) -> None:
    """/release 대상 없음 → 404."""
    client, _ = ctx
    r = client.post("/api/v1/live-monitor/no_such_strat/release",
                    json={"approved_by": "admin"})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# 10. 복수 전략 — 독립적으로 관리
# ---------------------------------------------------------------------------

def test_multiple_strategies_independent(ctx) -> None:
    """두 전략의 Kill Switch가 서로 독립적으로 동작한다."""
    client, factory = ctx
    _seed_trigger(factory, "strat_a")
    _seed_trigger(factory, "strat_b")

    # strat_a만 승인
    client.post("/api/v1/live-monitor/strat_a/approve-liquidation",
                json={"approved_by": "admin"})

    monitor = client.get("/api/v1/live-monitor").json()
    assert monitor["pending_approval_count"] == 1   # strat_b 아직 승인 대기
    assert monitor["pending_release_count"] == 1    # strat_a 해제 대기
