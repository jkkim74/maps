from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

_PASSWORD = "s3cret-pw"


@pytest.fixture
def auth_client(monkeypatch):
    """MAPS_AUTH_ENABLED=true 상태의 TestClient. 종료 시 설정 캐시를 원복한다."""
    monkeypatch.setenv("MAPS_AUTH_ENABLED", "true")
    monkeypatch.setenv("MAPS_AUTH_USERNAME", "admin")
    monkeypatch.setenv("MAPS_AUTH_PASSWORD", _PASSWORD)
    monkeypatch.setenv("MAPS_SESSION_SECRET_KEY", "test-session-secret-0123456789abcdef")
    from maps.common.settings import reload_settings

    reload_settings()
    from main import app

    client = TestClient(app)
    yield client

    # 인증 비활성으로 원복(다른 테스트 영향 방지). .env의 값을 노출하지 않도록 delenv 대신 명시적 false.
    monkeypatch.setenv("MAPS_AUTH_ENABLED", "false")
    reload_settings()


def test_unauthenticated_page_redirects_to_login(auth_client) -> None:
    res = auth_client.get("/", follow_redirects=False)
    assert res.status_code == 303
    assert res.headers["location"].startswith("/login")


def test_unauthenticated_api_returns_401(auth_client) -> None:
    res = auth_client.get("/api/v1/dashboard")
    assert res.status_code == 401


def test_health_is_public_even_with_auth(auth_client) -> None:
    res = auth_client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_login_page_is_public(auth_client) -> None:
    res = auth_client.get("/login")
    assert res.status_code == 200


def test_login_wrong_password_is_rejected(auth_client) -> None:
    res = auth_client.post(
        "/login",
        data={"username": "admin", "password": "wrong"},
        follow_redirects=False,
    )
    assert res.status_code == 401


def test_login_success_then_access_granted(auth_client) -> None:
    res = auth_client.post(
        "/login",
        data={"username": "admin", "password": _PASSWORD, "next": "/orders"},
        follow_redirects=False,
    )
    assert res.status_code == 303
    assert res.headers["location"] == "/orders"

    # 세션 쿠키 보유 → 보호 페이지 접근 허용
    page = auth_client.get("/", follow_redirects=False)
    assert page.status_code == 200


def test_logout_clears_session(auth_client) -> None:
    auth_client.post("/login", data={"username": "admin", "password": _PASSWORD})
    auth_client.get("/logout", follow_redirects=False)

    res = auth_client.get("/", follow_redirects=False)
    assert res.status_code == 303
    assert res.headers["location"].startswith("/login")


def test_open_redirect_is_sanitized(auth_client) -> None:
    res = auth_client.post(
        "/login",
        data={"username": "admin", "password": _PASSWORD, "next": "//evil.example.com"},
        follow_redirects=False,
    )
    assert res.status_code == 303
    assert res.headers["location"] == "/"


def test_auth_disabled_allows_access() -> None:
    # autouse 픽스처(_auth_disabled_by_default)가 인증을 끈 기본 상태에서
    # 보호 페이지가 그대로 열리는지 확인한다.
    from main import app

    client = TestClient(app)
    res = client.get("/", follow_redirects=False)
    assert res.status_code == 200
