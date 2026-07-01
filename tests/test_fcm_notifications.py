"""FCM 네이티브 푸시 알림 + 디바이스 토큰 등록 엔드포인트 테스트."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import maps.common.models  # noqa: F401
from maps.common.db import Base
from maps.common.models import DeviceToken


# ── 테스트 더블 ────────────────────────────────────────────────────────────────
class _FakeResp:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code


class _FakeHttp:
    """requests 대체 — post 호출을 기록한다."""

    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code
        self.calls: list[dict] = []

    def post(self, url: str, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return _FakeResp(self.status_code)


def _settings_with(monkeypatch, **env):
    """MAPS_FCM_* env를 설정하고 settings를 리로드해 반환한다."""
    from maps.common.settings import get_settings, reload_settings

    for key, value in env.items():
        monkeypatch.setenv(key, value)
    reload_settings()
    return get_settings()


# ── FcmNotifier ───────────────────────────────────────────────────────────────
def test_notifier_disabled_when_config_empty(monkeypatch) -> None:
    from maps.ops.notifications import FcmNotifier

    settings = _settings_with(
        monkeypatch, MAPS_ENV="production",
        MAPS_FCM_SERVICE_ACCOUNT_PATH="", MAPS_FCM_PROJECT_ID="",
    )
    http = _FakeHttp()
    fcm = FcmNotifier(settings=settings, http=http, token_provider=lambda: "tok")
    assert fcm.enabled is False
    assert fcm.send_to_token("dev1", title="t", body="b") is False
    assert http.calls == []  # 어떤 HTTP도 발생하지 않음


def test_notifier_send_to_token_payload(monkeypatch) -> None:
    from maps.ops.notifications import FcmNotifier

    settings = _settings_with(
        monkeypatch,
        MAPS_FCM_SERVICE_ACCOUNT_PATH="/tmp/sa.json",
        MAPS_FCM_PROJECT_ID="maps-proj",
        MAPS_FCM_ALLOW_NONPROD="true",
    )
    http = _FakeHttp()
    fcm = FcmNotifier(settings=settings, http=http, token_provider=lambda: "acc3ss")
    assert fcm.enabled is True

    assert fcm.send_to_token(
        "dev-token-1", title="편입", body="삼성전자", data={"type": "pick", "count": 1}
    ) is True

    assert len(http.calls) == 1
    call = http.calls[0]
    assert call["url"].endswith("/projects/maps-proj/messages:send")
    assert call["headers"]["Authorization"] == "Bearer acc3ss"
    message = call["json"]["message"]
    assert message["token"] == "dev-token-1"
    assert message["notification"] == {"title": "편입", "body": "삼성전자"}
    # data 값은 전부 문자열로 직렬화된다.
    assert message["data"] == {"type": "pick", "count": "1"}


def test_notifier_send_to_tokens_counts_success(monkeypatch) -> None:
    from maps.ops.notifications import FcmNotifier

    settings = _settings_with(
        monkeypatch,
        MAPS_FCM_SERVICE_ACCOUNT_PATH="/tmp/sa.json",
        MAPS_FCM_PROJECT_ID="maps-proj",
        MAPS_FCM_ALLOW_NONPROD="true",
    )
    http = _FakeHttp()
    fcm = FcmNotifier(settings=settings, http=http, token_provider=lambda: "acc3ss")
    sent = fcm.send_to_tokens(["a", "", "b"], title="t", body="b")
    assert sent == 2  # 빈 토큰은 건너뜀
    assert len(http.calls) == 2


def test_notifier_http_failure_is_swallowed(monkeypatch) -> None:
    from maps.ops.notifications import FcmNotifier

    settings = _settings_with(
        monkeypatch,
        MAPS_FCM_SERVICE_ACCOUNT_PATH="/tmp/sa.json",
        MAPS_FCM_PROJECT_ID="maps-proj",
        MAPS_FCM_ALLOW_NONPROD="true",
    )

    class _Boom:
        def post(self, *a, **k):
            raise RuntimeError("network down")

    fcm = FcmNotifier(settings=settings, http=_Boom(), token_provider=lambda: "t")
    assert fcm.send_to_token("dev", title="t", body="b") is False


def test_notifier_no_token_skips_send(monkeypatch) -> None:
    """액세스 토큰을 못 받으면(자격증명 실패) 실발송 없이 False."""
    from maps.ops.notifications import FcmNotifier

    settings = _settings_with(
        monkeypatch,
        MAPS_FCM_SERVICE_ACCOUNT_PATH="/tmp/sa.json",
        MAPS_FCM_PROJECT_ID="maps-proj",
        MAPS_FCM_ALLOW_NONPROD="true",
    )
    http = _FakeHttp()
    fcm = FcmNotifier(settings=settings, http=http, token_provider=lambda: None)
    assert fcm.send_to_token("dev", title="t", body="b") is False
    assert http.calls == []


def test_nonprod_blocks_real_send_even_with_config(monkeypatch) -> None:
    """비운영 환경에서는 설정이 완비돼도 실발송을 막는다(더미 알림 유출 방지)."""
    from maps.ops.notifications import FcmNotifier

    settings = _settings_with(
        monkeypatch, MAPS_ENV="development",
        MAPS_FCM_SERVICE_ACCOUNT_PATH="/tmp/sa.json",
        MAPS_FCM_PROJECT_ID="maps-proj",
    )
    http = _FakeHttp()
    fcm = FcmNotifier(settings=settings, http=http, token_provider=lambda: "tok")
    assert fcm.enabled is False
    assert fcm.send_to_token("dev", title="t", body="b") is False
    assert fcm.send_to_tokens(["a", "b"], title="t", body="b") == 0
    assert http.calls == []


def test_production_allows_real_send(monkeypatch) -> None:
    """운영 환경에서는 별도 플래그 없이 정상 발송한다."""
    from maps.ops.notifications import FcmNotifier

    settings = _settings_with(
        monkeypatch, MAPS_ENV="production",
        MAPS_FCM_SERVICE_ACCOUNT_PATH="/tmp/sa.json",
        MAPS_FCM_PROJECT_ID="maps-proj",
    )
    http = _FakeHttp()
    fcm = FcmNotifier(settings=settings, http=http, token_provider=lambda: "tok")
    assert fcm.enabled is True
    assert fcm.send_to_token("dev", title="t", body="b") is True
    assert len(http.calls) == 1


def test_send_analysis_picks_pushes_summary(monkeypatch) -> None:
    from maps.ops.notifications import FcmNotifier

    settings = _settings_with(
        monkeypatch, MAPS_ENV="production",
        MAPS_FCM_SERVICE_ACCOUNT_PATH="/tmp/sa.json",
        MAPS_FCM_PROJECT_ID="maps-proj",
    )
    http = _FakeHttp()
    fcm = FcmNotifier(settings=settings, http=http, token_provider=lambda: "tok")
    picks = [{"ticker": "005930", "name": "삼성전자"}]
    sent = fcm.send_analysis_picks(["dev1", "dev2"], picks, regime="mixed", ref_date="2026-06-29")
    assert sent == 2
    assert len(http.calls) == 2
    message = http.calls[0]["json"]["message"]
    assert message["notification"]["title"] == "MAPS 편입 1종목"
    assert message["data"]["type"] == "analysis_picks"
    assert message["data"]["ref_date"] == "2026-06-29"


# ── 디바이스 토큰 등록 엔드포인트 ─────────────────────────────────────────────
@pytest.fixture
def client():
    """인메모리 SQLite에 device_token 테이블을 만들고 TestClient를 만든다."""
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
    test_client = TestClient(app, raise_server_exceptions=True)
    test_client.session_factory = factory
    yield test_client

    app.dependency_overrides.pop(get_db, None)
    Base.metadata.drop_all(engine)
    engine.dispose()


def test_register_device_token_creates_row(client) -> None:
    r = client.post("/api/v1/mobile/device-token", json={"token": "abc", "platform": "android"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "registered"
    assert body["active"] is True
    with client.session_factory() as s:
        row = s.query(DeviceToken).filter(DeviceToken.token == "abc").one()
        assert row.platform == "android" and row.active is True


def test_register_device_token_is_upsert(client) -> None:
    client.post("/api/v1/mobile/device-token", json={"token": "abc", "platform": "android"})
    r = client.post("/api/v1/mobile/device-token", json={"token": "abc", "platform": "ios"})
    assert r.status_code == 200
    assert r.json()["status"] == "updated"
    with client.session_factory() as s:
        rows = s.query(DeviceToken).filter(DeviceToken.token == "abc").all()
        assert len(rows) == 1  # 중복 행 없음
        assert rows[0].platform == "ios" and rows[0].active is True


def test_register_device_token_rejects_empty(client) -> None:
    r = client.post("/api/v1/mobile/device-token", json={"token": "  ", "platform": "android"})
    assert r.status_code == 400


def test_deregister_device_token_deactivates(client) -> None:
    client.post("/api/v1/mobile/device-token", json={"token": "abc", "platform": "android"})
    r = client.request("DELETE", "/api/v1/mobile/device-token", json={"token": "abc"})
    assert r.status_code == 200
    assert r.json()["status"] == "deregistered"
    with client.session_factory() as s:
        row = s.query(DeviceToken).filter(DeviceToken.token == "abc").one()
        assert row.active is False


def test_deregister_unknown_token_is_idempotent(client) -> None:
    r = client.request("DELETE", "/api/v1/mobile/device-token", json={"token": "nope"})
    assert r.status_code == 200
    assert r.json()["status"] == "not_found"
