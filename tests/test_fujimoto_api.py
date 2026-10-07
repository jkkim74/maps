"""Admin plus explicit owner-scoped Fujimoto API tests; no broker calls."""
from fastapi.testclient import TestClient
import main
from maps.api.deps import get_db
from maps.api.auth import Identity
from maps.execution.safety import account_key
from maps.fujimoto.domain import Mode
from maps.fujimoto.repository import FujimotoRepository
import pytest


@pytest.fixture
def db():
    """Use one isolated SQLite connection across FastAPI worker threads."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool
    from maps.common.db import Base
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def test_status_defaults_observe_and_view_renders(db):
    main.app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(main.app)
        response = client.get("/api/v1/fujimoto/status")
        assert response.status_code == 200
        assert response.json()["control"]["execution_mode"] == "observe"
        assert response.json()["modes"][0]["budget"] is None
        assert client.get("/fujimoto").status_code == 200
    finally:
        main.app.dependency_overrides.clear()


def test_foreign_owner_cannot_read_or_stop_cycles(db, monkeypatch):
    from maps.api import fujimoto
    config = FujimotoRepository(db).configure(account_key(), 7, Mode.SAFE, 500000)
    cycle = FujimotoRepository(db).create_cycle(config.id, "AAA")
    db.commit()
    monkeypatch.setattr(fujimoto, "current_identity", lambda request: Identity(8, "other", "admin"))
    main.app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(main.app)
        assert client.get(f"/api/v1/fujimoto/cycles/{cycle.id}").status_code in (403, 404)
        assert client.post("/api/v1/fujimoto/stop").status_code == 403
    finally:
        main.app.dependency_overrides.clear()


def test_configure_split_is_explicit_and_activation_never_manufactures_pass(db):
    main.app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(main.app)
        response = client.put("/api/v1/fujimoto/config", json={"budget": 1000000, "with_orderbook": False})
        assert response.status_code == 200
        assert [m["budget"] for m in client.get("/api/v1/fujimoto/status").json()["modes"]] == [500000, 500000]
        reopened = TestClient(main.app).get("/api/v1/fujimoto/status").json()
        assert all(m["settings"]["with_orderbook"] is False for m in reopened["modes"])
        template = client.get("/fujimoto").text
        assert "$('fj-book').checked=s.modes[0].settings.with_orderbook" in template
        assert "if(syncForm)" in template and "syncForm=false" in template
        response = client.post("/api/v1/fujimoto/activate", json={"execution_mode": "paper", "replay_id": 123, "sell_consent": True})
        assert response.status_code == 409
        assert client.get("/api/v1/fujimoto/status").json()["control"]["entries_enabled"] is False
    finally:
        main.app.dependency_overrides.clear()
