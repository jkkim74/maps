"""회원 계정 API와 역할 게이트 계약 테스트."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from maps.api.auth import is_allowed
from tests.test_auth import add_user, make_account_store

_ADMIN_PW = "admin-pw-1234"
_USER_PW = "member-pw-1234"


@pytest.fixture
def client(tmp_path, monkeypatch):
    """인증이 켜진 TestClient + 관리자·일반 사용자 계정 2개."""
    monkeypatch.setenv("MAPS_AUTH_ENABLED", "true")
    monkeypatch.setenv("MAPS_AUTH_USERNAME", "admin")
    monkeypatch.setenv("MAPS_AUTH_PASSWORD", _ADMIN_PW)
    monkeypatch.setenv("MAPS_SESSION_SECRET_KEY", "test-session-secret-0123456789abcdef")
    from maps.common.settings import reload_settings

    reload_settings()
    factory = make_account_store(tmp_path, monkeypatch, name="users.db")
    add_user(factory, "admin", _ADMIN_PW, role="admin")
    add_user(factory, "member", _USER_PW, role="user")
    from main import app

    with TestClient(app) as test_client:
        yield test_client

    monkeypatch.setenv("MAPS_AUTH_ENABLED", "false")
    reload_settings()


def login(client: TestClient, username: str, password: str) -> None:
    """세션 쿠키를 얻는다."""
    res = client.post(
        "/login", data={"username": username, "password": password}, follow_redirects=False
    )
    assert res.status_code == 303


# ── 역할 게이트 (순수 함수) ───────────────────────────────────────────────────
def test_admin_may_use_every_path() -> None:
    assert is_allowed("admin", "/ops-config", "GET") is True
    assert is_allowed("admin", "/api/v1/analysis-picks/1/arm-plan", "POST") is True


def test_user_allowlist_is_fail_closed() -> None:
    """목록에 없는 경로는 전부 막힌다 — 새 라우터가 실수로 열리지 않는다."""
    assert is_allowed("user", "/candidates", "GET") is True
    assert is_allowed("user", "/api/v1/candidates", "GET") is True
    assert is_allowed("user", "/ops-config", "GET") is False
    assert is_allowed("user", "/api/v1/orders", "GET") is False
    assert is_allowed("user", "/api/v1/some-future-router", "GET") is False


def test_user_may_read_picks_but_not_change_them() -> None:
    """상태를 바꾸는 동작은 POST 라 조회만 열려 있어도 무장이 막힌다."""
    assert is_allowed("user", "/api/v1/analysis-picks", "GET") is True
    assert is_allowed("user", "/api/v1/analysis-picks/1/arm-plan", "POST") is False
    assert is_allowed("user", "/api/v1/analysis-picks/1", "DELETE") is False


# ── 게이트 실제 동작 ──────────────────────────────────────────────────────────
def test_general_user_is_blocked_from_admin_screens(client: TestClient) -> None:
    login(client, "member", _USER_PW)

    assert client.get("/ops-config").status_code == 403
    assert client.get("/api/v1/orders").status_code == 403
    assert client.get("/api/v1/users").status_code == 403


def test_general_user_reaches_own_screens(client: TestClient) -> None:
    login(client, "member", _USER_PW)

    assert client.get("/settings").status_code == 200
    assert client.get("/api/v1/users/me").status_code == 200


def test_root_redirects_general_user_away_from_operator_dashboard(client: TestClient) -> None:
    """대시보드는 운영자 잔고를 그린다 — 일반 사용자는 렌더링되면 안 된다."""
    login(client, "member", _USER_PW)

    res = client.get("/", follow_redirects=False)
    assert res.status_code == 303
    assert res.headers["location"] == "/stock-analysis"


def test_disabled_account_cannot_log_in_or_reuse_session(client: TestClient) -> None:
    """계정을 비활성화하면 이미 로그인한 세션도 즉시 막힌다."""
    login(client, "member", _USER_PW)
    assert client.get("/api/v1/users/me").status_code == 200

    admin = TestClient(client.app)
    login(admin, "admin", _ADMIN_PW)
    member_id = next(u["id"] for u in admin.get("/api/v1/users").json() if u["username"] == "member")
    assert admin.put(f"/api/v1/users/{member_id}", json={"status": "disabled"}).status_code == 200

    assert client.get("/api/v1/users/me").status_code == 401


# ── 사용자 API ────────────────────────────────────────────────────────────────
def test_admin_creates_user_and_password_hash_is_never_returned(client: TestClient) -> None:
    login(client, "admin", _ADMIN_PW)

    res = client.post(
        "/api/v1/users",
        json={"username": "newbie", "password": "newbie-pw-1", "role": "user"},
    )
    assert res.status_code == 201
    assert "password_hash" not in res.json()
    assert "password" not in res.json()

    duplicate = client.post(
        "/api/v1/users", json={"username": "newbie", "password": "another-pw-1"}
    )
    assert duplicate.status_code == 409


def test_short_password_is_rejected(client: TestClient) -> None:
    login(client, "admin", _ADMIN_PW)

    res = client.post("/api/v1/users", json={"username": "shorty", "password": "abc"})
    assert res.status_code == 400


def test_last_admin_cannot_be_demoted_or_disabled(client: TestClient) -> None:
    """마지막 관리자를 잠그면 아무도 운영 화면에 들어갈 수 없게 된다."""
    login(client, "admin", _ADMIN_PW)
    admin_id = next(u["id"] for u in client.get("/api/v1/users").json() if u["username"] == "admin")

    assert client.put(f"/api/v1/users/{admin_id}", json={"role": "user"}).status_code == 400
    assert client.put(f"/api/v1/users/{admin_id}", json={"status": "disabled"}).status_code == 400


def test_preferences_round_trip_and_reject_unknown_keys(client: TestClient) -> None:
    login(client, "member", _USER_PW)

    saved = client.put(
        "/api/v1/users/me/preferences",
        json={
            "landing_screen": "candidates",
            "candidate_min_score": 42.5,
            "candidate_markets": ["KOSPI"],
        },
    )
    assert saved.status_code == 200
    assert saved.json()["preferences"]["landing_screen"] == "candidates"
    assert saved.json()["preferences"]["candidate_min_score"] == 42.5

    fetched = client.get("/api/v1/users/me").json()["preferences"]
    assert fetched["candidate_markets"] == ["KOSPI"]
    rejected = client.put("/api/v1/users/me/preferences", json={"maps_broker_mode": "kis"})
    assert rejected.status_code == 422


def test_preferences_landing_screen_is_used_after_login(client: TestClient) -> None:
    login(client, "member", _USER_PW)
    client.put("/api/v1/users/me/preferences", json={"landing_screen": "candidates"})
    client.get("/logout")

    res = client.post(
        "/login", data={"username": "member", "password": _USER_PW}, follow_redirects=False
    )
    assert res.headers["location"] == "/candidates"


def test_password_change_requires_current_password(client: TestClient) -> None:
    login(client, "member", _USER_PW)

    wrong = client.post(
        "/api/v1/users/me/password",
        json={"current_password": "nope", "new_password": "brand-new-pw"},
    )
    assert wrong.status_code == 400

    ok = client.post(
        "/api/v1/users/me/password",
        json={"current_password": _USER_PW, "new_password": "brand-new-pw"},
    )
    assert ok.status_code == 200

    client.get("/logout")
    stale = client.post(
        "/login", data={"username": "member", "password": _USER_PW}, follow_redirects=False
    )
    assert stale.status_code == 401


def test_admin_reset_password_returns_temporary_password_once(client: TestClient) -> None:
    login(client, "admin", _ADMIN_PW)
    member_id = next(
        u["id"] for u in client.get("/api/v1/users").json() if u["username"] == "member"
    )

    res = client.post(f"/api/v1/users/{member_id}/reset-password")
    assert res.status_code == 200
    temporary = res.json()["temporary_password"]
    assert len(temporary) >= 8

    client.get("/logout")
    login(client, "member", temporary)


def test_bootstrap_admin_is_seeded_only_when_no_account_exists(tmp_path, monkeypatch) -> None:
    """빈 DB 에서만 .env 자격증명으로 관리자를 만든다."""
    monkeypatch.setenv("MAPS_AUTH_ENABLED", "true")
    monkeypatch.setenv("MAPS_AUTH_USERNAME", "operator")
    monkeypatch.setenv("MAPS_AUTH_PASSWORD", "bootstrap-pw-1")
    from maps.common.settings import reload_settings

    reload_settings()
    factory = make_account_store(tmp_path, monkeypatch, name="bootstrap.db")

    from maps.api.auth import ensure_bootstrap_admin
    from maps.common.models import AppUser

    ensure_bootstrap_admin()
    session = factory()
    try:
        seeded = session.query(AppUser).all()
        assert [u.username for u in seeded] == ["operator"]
        assert seeded[0].role == "admin"
        assert seeded[0].password_hash != "bootstrap-pw-1"
    finally:
        session.close()

    # 두 번째 호출은 아무것도 하지 않는다.
    ensure_bootstrap_admin()
    session = factory()
    try:
        assert session.query(AppUser).count() == 1
    finally:
        session.close()

    monkeypatch.setenv("MAPS_AUTH_ENABLED", "false")
    reload_settings()


# ── 일일 AI 분석 한도 ─────────────────────────────────────────────────────────
def test_analysis_quota_blocks_before_calling_ai(client: TestClient, monkeypatch) -> None:
    """한도를 다 쓰면 429 이고, 그 전에 AI/분석기를 호출하지 않는다(비용 방지)."""
    from maps.stock_analysis import analyzer

    called: list[str] = []
    monkeypatch.setattr(
        analyzer, "analyze", lambda *args, **kwargs: called.append("analyze") or {}
    )

    login(client, "admin", _ADMIN_PW)
    member_id = next(
        u["id"] for u in client.get("/api/v1/users").json() if u["username"] == "member"
    )
    assert client.put(
        f"/api/v1/users/{member_id}", json={"daily_analysis_limit": 0}
    ).status_code == 200
    client.get("/logout")

    login(client, "member", _USER_PW)
    res = client.post("/api/v1/stock-analysis/analyze", json={"ticker": "005930"})

    assert res.status_code == 429
    assert called == []


def test_admin_has_no_analysis_quota(client: TestClient) -> None:
    """운영자는 한도를 적용받지 않는다."""
    from maps.common.user_prefs import daily_analysis_limit
    from maps.common.models import AppUser
    from maps.common import db as db_module

    session = db_module.SessionLocal()
    try:
        admin = session.query(AppUser).filter(AppUser.username == "admin").one()
        member = session.query(AppUser).filter(AppUser.username == "member").one()
        assert daily_analysis_limit(admin) == -1
        assert daily_analysis_limit(member) > 0
    finally:
        session.close()


def test_general_user_sees_only_own_analysis_history(client: TestClient) -> None:
    """소유자가 없는 기존 행(운영자 데이터)은 일반 사용자에게 보이지 않는다."""
    import datetime

    from maps.common import db as db_module
    from maps.common.models import AppUser, StockAnalysisHistory

    session = db_module.SessionLocal()
    try:
        member_id = session.query(AppUser).filter(AppUser.username == "member").one().id
        session.add_all([
            StockAnalysisHistory(
                owner_user_id=None, ticker="005930", name="삼성전자",
                ref_date=datetime.date(2026, 8, 12), snapshot={}, narrative="",
                trade_plan={}, recommendation="WATCH",
            ),
            StockAnalysisHistory(
                owner_user_id=member_id, ticker="000660", name="SK하이닉스",
                ref_date=datetime.date(2026, 8, 12), snapshot={}, narrative="",
                trade_plan={}, recommendation="BUY",
            ),
        ])
        session.commit()
    finally:
        session.close()

    login(client, "member", _USER_PW)
    tickers = [i["ticker"] for i in client.get("/api/v1/stock-analysis/history").json()["items"]]
    assert tickers == ["000660"]

    client.get("/logout")
    login(client, "admin", _ADMIN_PW)
    admin_tickers = [
        i["ticker"] for i in client.get("/api/v1/stock-analysis/history").json()["items"]
    ]
    assert set(admin_tickers) == {"005930", "000660"}


def test_removed_notification_prefs_are_rejected(client: TestClient) -> None:
    """삭제한 알림 키는 extra=forbid 로 거절된다 — 조용히 무시되면 안 된다."""
    login(client, "member", _USER_PW)

    response = client.put(
        "/api/v1/users/me/preferences",
        json={"landing_screen": "candidates", "notify_push": True},
    )
    assert response.status_code == 422


def test_preferences_no_longer_expose_notification_keys(client: TestClient) -> None:
    """응답에서도 알림 키가 사라져야 한다."""
    login(client, "member", _USER_PW)

    prefs = client.get("/api/v1/users/me").json()["preferences"]
    assert "notify_push" not in prefs
    assert "notify_telegram" not in prefs
    assert "telegram_chat_id" not in prefs
