"""모바일 앱 토큰 인증(Bearer) 테스트."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import maps.common.models  # noqa: F401
from maps.common.db import Base


def _enable_auth(monkeypatch, *, password="pw123", username="admin"):
    """인증을 켜고 자격증명/시크릿을 설정한 뒤 settings를 리로드한다."""
    from maps.common.settings import reload_settings

    monkeypatch.setenv("MAPS_AUTH_ENABLED", "true")
    monkeypatch.setenv("MAPS_AUTH_USERNAME", username)
    monkeypatch.setenv("MAPS_AUTH_PASSWORD", password)
    monkeypatch.setenv("MAPS_SESSION_SECRET_KEY", "fixed-test-secret")
    reload_settings()


@pytest.fixture
def client(monkeypatch):
    from main import app
    from maps.api.deps import get_db
    from maps.common import db as db_module
    from maps.common.models import AppUser
    from maps.common.passwords import hash_password

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # 모바일 로그인은 계정 저장소를 본다. 인증 게이트도 같은 저장소를 보게 맞춘다.
    seed = factory()
    try:
        seed.add(
            AppUser(username="admin", password_hash=hash_password("pw123"), role="admin")
        )
        seed.commit()
    finally:
        seed.close()
    monkeypatch.setattr(db_module, "SessionLocal", factory)

    def override_get_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app, raise_server_exceptions=True)
    app.dependency_overrides.pop(get_db, None)
    Base.metadata.drop_all(engine)
    engine.dispose()


# ── 토큰 유틸 단위 ──────────────────────────────────────────────────────────────
def test_token_roundtrip(monkeypatch) -> None:
    _enable_auth(monkeypatch)
    from maps.api.auth import make_mobile_token, verify_mobile_token

    tok = make_mobile_token("admin")
    assert verify_mobile_token(tok) == "admin"


def test_token_tampered_rejected(monkeypatch) -> None:
    _enable_auth(monkeypatch)
    from maps.api.auth import make_mobile_token, verify_mobile_token

    tok = make_mobile_token("admin")
    assert verify_mobile_token(tok + "x") is None
    assert verify_mobile_token("garbage") is None


# ── 로그인 엔드포인트 ──────────────────────────────────────────────────────────
def test_login_wrong_password(client, monkeypatch) -> None:
    _enable_auth(monkeypatch)
    r = client.post("/api/v1/mobile/login", json={"username": "admin", "password": "nope"})
    assert r.status_code == 401


def test_login_success_returns_token(client, monkeypatch) -> None:
    _enable_auth(monkeypatch)
    r = client.post("/api/v1/mobile/login", json={"username": "admin", "password": "pw123"})
    assert r.status_code == 200
    body = r.json()
    assert body["username"] == "admin" and body["token"]


def test_login_endpoint_is_public_even_when_auth_on(client, monkeypatch) -> None:
    """로그인 자체는 토큰 없이 접근 가능해야 한다(공개경로)."""
    _enable_auth(monkeypatch)
    r = client.post("/api/v1/mobile/login", json={"username": "admin", "password": "pw123"})
    assert r.status_code == 200  # 401 아님 → 게이트가 막지 않음


# ── 게이트(Bearer) ─────────────────────────────────────────────────────────────
def test_summary_blocked_without_token(client, monkeypatch) -> None:
    _enable_auth(monkeypatch)
    r = client.get("/api/v1/mobile/summary")
    assert r.status_code == 401


def test_summary_allowed_with_token(client, monkeypatch) -> None:
    _enable_auth(monkeypatch)
    tok = client.post(
        "/api/v1/mobile/login", json={"username": "admin", "password": "pw123"}
    ).json()["token"]
    r = client.get("/api/v1/mobile/summary", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    assert "dashboard" in r.json()


def test_summary_rejects_bad_token(client, monkeypatch) -> None:
    _enable_auth(monkeypatch)
    r = client.get("/api/v1/mobile/summary", headers={"Authorization": "Bearer not-a-token"})
    assert r.status_code == 401


def test_auth_disabled_allows_summary_without_token(client) -> None:
    """인증 비활성(기본 conftest)에서는 토큰 없이도 summary가 열린다."""
    r = client.get("/api/v1/mobile/summary")
    assert r.status_code == 200
