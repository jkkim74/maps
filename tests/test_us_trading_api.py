"""US HTTP boundaries reject untrusted execution metadata and non-admin actors."""
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from maps.api.auth import Identity


@pytest.fixture
def client():
    from maps.api.us_trading import router, get_service

    app = FastAPI()
    captured = []

    class Service:
        def status(self):
            return {"environment": "mock", "symbols": [], "block_reasons": ["disabled"]}

        def preview(self, order, context):
            captured.append((order, context))
            return {"allowed": True, "notional_usd": str(order.quantity * order.limit_price)}

        def submit(self, order, context):
            captured.append((order, context))
            return {"id": "test-order", "status": "pending"}

    @app.middleware("http")
    async def identity(request: Request, call_next):
        request.state.user = Identity(id=1, username="tester", role=request.headers.get("x-test-role", "admin"))
        return await call_next(request)

    app.include_router(router)
    app.dependency_overrides[get_service] = lambda: Service()
    with TestClient(app) as c:
        yield SimpleNamespace(http=c, captured=captured)


def payload(**changes):
    return {"ticker": "AAPL", "exchange": "NASD", "side": "buy", "quantity": 2,
            "limit_price": "12.34", "request_id": "event-123", "sell_consent": False, **changes}


def test_admin_status_read_and_member_denied(client):
    assert client.http.get("/api/v1/us-trading/status").status_code == 200
    assert client.http.get("/api/v1/us-trading/status", headers={"x-test-role": "user"}).status_code == 403


@pytest.mark.parametrize("changes", [{"source": "strategy"}, {"account": "other"},
    {"quantity": 1.5}, {"quantity": True}, {"quantity": 0}, {"limit_price": "NaN"},
    {"limit_price": "0"}, {"exchange": "KRX"}, {"side": "short"}])
def test_invalid_order_never_reaches_service(client, changes):
    response = client.http.post("/api/v1/us-trading/orders", json=payload(**changes))
    assert response.status_code == 422
    assert client.captured == []


def test_preview_preserves_decimal_and_server_assigned_context(client):
    response = client.http.post("/api/v1/us-trading/orders/preview", json=payload())
    assert response.status_code == 200
    order, context = client.captured[0]
    assert order.limit_price == Decimal("12.34")
    assert order.market == "US" and order.currency == "USD"
    assert context.source == "manual" and context.actor == "tester" and context.is_admin
    assert context.event_key == "event-123"


def test_sell_requires_explicit_consent(client):
    response = client.http.post("/api/v1/us-trading/orders", json=payload(side="sell"))
    assert response.status_code == 422
    assert not client.captured


def test_member_cannot_submit(client):
    response = client.http.post("/api/v1/us-trading/orders", json=payload(), headers={"x-test-role": "user"})
    assert response.status_code == 403
    assert not client.captured


def test_default_read_views_work_without_credentials_or_broker(tmp_path):
    """Disabled screen health must remain readable without touching any broker."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from maps.common.db import Base
    from maps.common.settings import MapsSettings
    from maps.execution.us_service import USExecutionService
    from maps.api.us_trading import router, get_service
    engine = create_engine(f"sqlite:///{tmp_path / 'read.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    class NoBroker:
        def __getattr__(self, key):
            raise AssertionError("read view called broker: " + key)
    app = FastAPI()
    app.include_router(router)
    def service():
        with Session(engine) as db:
            yield USExecutionService(db, NoBroker(), MapsSettings(_env_file=None))
    app.dependency_overrides[get_service] = service
    with TestClient(app) as http:
        assert http.get("/api/v1/us-trading/status").status_code == 200
        response = http.get("/api/v1/us-trading/account")
        assert response.status_code == 200
        assert response.json()["cash_usd"] is None
        assert http.get("/api/v1/us-trading/positions").json() == {"items": []}
    engine.dispose()
