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


def test_paper_test_schema_forbids_missing_false_and_caller_evidence(db):
    """Only true explicit consent is accepted; measured activation still needs replay."""
    main.app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(main.app)
        for payload in ({}, {"sell_consent": False}, {"sell_consent": 1}, {"sell_consent": "true"},
                        {"sell_consent": True, "pass": True},
                        {"sell_consent": True, "expires_at": "2099-01-01"},
                        {"sell_consent": True, "environment": "paper"}):
            assert client.post("/api/v1/fujimoto/activate-paper-test", json=payload).status_code == 422
        assert client.post("/api/v1/fujimoto/activate-paper-test", json={"sell_consent": True}).status_code == 409
        assert client.post("/api/v1/fujimoto/activate", json={"execution_mode": "paper", "sell_consent": True}).status_code == 422
    finally:
        main.app.dependency_overrides.clear()


def test_paper_status_and_cumulative_orders_are_persisted_read_only(db, monkeypatch):
    from maps.common.models import FujimotoEvidence
    from maps.fujimoto.domain import Decision
    from maps.fujimoto.repository import FillEvent
    from datetime import datetime, timezone, date
    from maps.execution import broker_adapter
    monkeypatch.setattr(broker_adapter, "get_broker", lambda *a, **kw: pytest.fail("read contacted broker"))
    repo = FujimotoRepository(db)
    config = repo.configure(account_key(), None, Mode.ORIGINAL, 5000000)
    cycle = repo.create_cycle(config.id, "005930")
    now = datetime.now(timezone.utc)
    source = repo.record_evidence("execution_decision", cycle.ticker, now, now, {"authorization_id": "trial"})
    order = repo.reserve_order(cycle.id, Decision("buy", "fixture", buy_stage=1, buy_weight=1,
        price_cap=1000, timing="next_session"), source.id, 2, 1000)
    repo.apply_fill(FillEvent(order.id, account_key(), None, 1, 1000, 1, 0, "UNKNOWN", date.today()))
    db.commit()
    before = db.query(FujimotoEvidence).count()
    main.app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(main.app)
        data = client.get("/api/v1/fujimoto/status").json()
        assert data["paper_test"]["state"] == "not_started"
        assert data["paper_test"]["eligible"] is False
        rows = data["modes"][1]["cycles"][0]["orders"]
        assert rows[0]["status"] == "UNKNOWN"
        assert rows[0]["filled_quantity"] == rows[0]["remaining_quantity"] == 1
        assert rows[0]["gross"] == 1000 and rows[0]["reserved_cash"] > 1000
        assert rows[0]["authorization_id"] == "trial"
        assert db.query(FujimotoEvidence).count() == before
    finally:
        main.app.dependency_overrides.clear()


def test_trial_order_view_distinguishes_terminal_remainder_from_live_reservation(db):
    """UI status and detail show confirmed cancellation without inventing a fill."""
    from datetime import date, datetime, timezone
    from maps.fujimoto.domain import Decision
    from maps.fujimoto.repository import FillEvent
    repo = FujimotoRepository(db)
    config = repo.configure(account_key(), None, Mode.SAFE, 5000000)
    cycle = repo.create_cycle(config.id, "005930")
    now = datetime.now(timezone.utc)
    evidence = repo.record_evidence("execution_decision", cycle.ticker, now, now,
        {"authorization_id": "trial-ui"})
    order = repo.reserve_order(cycle.id, Decision("buy", "fixture", buy_stage=1,
        buy_weight=1, price_cap=1000, timing="next_session"), evidence.id, 3, 1000)
    repo.apply_fill(FillEvent(order.id, account_key(), None, 1, 1000, 1, 0,
        "CANCELLED", date.today()))
    db.commit()
    main.app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(main.app)
        status_row = client.get("/api/v1/fujimoto/status").json()["modes"][0]["cycles"][0]
        detail_row = client.get(f"/api/v1/fujimoto/cycles/{cycle.id}").json()
        assert status_row == detail_row
        assert detail_row["state"]["quantity"] == 1
        row = detail_row["orders"][0]
        assert row["status"] == "CANCELLED"
        assert row["quantity"] == 3 and row["filled_quantity"] == 1
        assert row["remaining_quantity"] == 2
        assert row["reserved_cash"] == row["reserved_quantity"] == 0
        assert row["gross"] == 1000 and row["fees"] == 1 and row["tax"] == 0
        assert row["authorization_id"] == "trial-ui"
    finally:
        main.app.dependency_overrides.clear()
