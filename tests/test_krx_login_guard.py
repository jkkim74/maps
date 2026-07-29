"""KRX 로그인 회로차단기 테스트.

2026-07-27 운영 사고 재현: 만료된 자격증명으로 158 회 재로그인을 시도해
KRX 계정이 오류횟수 초과로 잠겼다. 이 스위트는 그 재시도가 실제로 끊기는지,
그리고 차단 중에는 **HTTP 요청 자체가 나가지 않는지**를 검증한다.

계정이 정상인 지금은 실제 재현이 불가능하므로 pykrx 인증 모듈을 mock 으로
갈아끼워 검증한다 (테스트가 네트워크를 타지 않는다).
"""

from __future__ import annotations

import sys
import types

import pytest

from maps.data import krx_auth
from maps.data.krx_auth import (
    TERMINAL_ERROR_CODES,
    KRXLoginBreaker,
    describe_error_code,
)


@pytest.fixture
def breaker() -> KRXLoginBreaker:
    """짧은 쿨다운을 가진 독립 회로차단기."""
    return KRXLoginBreaker(
        max_failures=3, base_cooldown_seconds=100.0, max_cooldown_seconds=400.0
    )


@pytest.fixture
def fake_clock(monkeypatch):
    """``time.monotonic`` 을 수동으로 진행시키는 가짜 시계."""

    class Clock:
        def __init__(self) -> None:
            self.now = 1000.0

        def advance(self, seconds: float) -> None:
            self.now += seconds

    clock = Clock()
    monkeypatch.setattr(krx_auth.time, "monotonic", lambda: clock.now)
    return clock


# ── 회로차단기 상태 전이 ──────────────────────────────────────────────────────


def test_healthy_breaker_allows_login(breaker):
    """실패가 없으면 로그인을 허용한다."""
    assert breaker.allow() is True
    assert breaker.status()["blocked"] is False


@pytest.mark.parametrize("code", sorted(TERMINAL_ERROR_CODES))
def test_terminal_code_opens_circuit_on_first_failure(breaker, code):
    """CD007(잠금)·CD010(변경필요)은 1회 실패로 즉시 차단한다.

    이 코드들은 재시도로 회복되지 않으며, 재시도 누적이 계정 잠금을 유발한다.
    """
    breaker.record_failure(code)

    assert breaker.allow() is False
    status = breaker.status()
    assert status["blocked"] is True
    assert status["last_error_code"] == code
    # 치명 코드는 곧바로 최장 쿨다운을 쓴다.
    assert status["cooldown_remaining_seconds"] == pytest.approx(400.0, abs=1.0)


def test_terminal_code_blocks_repeated_attempts(breaker):
    """차단 이후의 반복 시도는 모두 억제되고 카운트된다 (158회 재시도 재현)."""
    breaker.record_failure("CD010")

    assert all(breaker.allow() is False for _ in range(158))
    assert breaker.status()["suppressed_attempts"] == 158


def test_transient_failures_open_only_after_threshold(breaker):
    """네트워크 오류 등 일시 실패는 임계값까지는 재시도를 허용한다."""
    breaker.record_failure("NETWORK")
    assert breaker.allow() is True
    breaker.record_failure("NETWORK")
    assert breaker.allow() is True

    breaker.record_failure("NETWORK")
    assert breaker.allow() is False
    assert breaker.status()["consecutive_failures"] == 3


def test_cooldown_expiry_allows_one_retry(breaker, fake_clock):
    """쿨다운이 끝나면 다시 시도를 허용한다 (영구 차단이 아니다)."""
    for _ in range(3):
        breaker.record_failure("NETWORK")
    assert breaker.allow() is False

    fake_clock.advance(99.0)
    assert breaker.allow() is False

    fake_clock.advance(2.0)
    assert breaker.allow() is True
    assert breaker.status()["suppressed_attempts"] == 0


def test_cooldown_doubles_on_each_reopen(breaker, fake_clock):
    """회로가 다시 열릴 때마다 쿨다운이 2배로 늘고 상한에서 멈춘다."""
    # 첫 차단까지는 max_failures 회가 필요하고, 회로가 열린 뒤에는 실패 1회마다 다시 열린다.
    for _ in range(3):
        breaker.record_failure("NETWORK")

    observed: list[float] = []
    for _ in range(4):
        remaining = breaker.status()["cooldown_remaining_seconds"]
        observed.append(remaining)
        fake_clock.advance(remaining + 1.0)
        assert breaker.allow() is True
        breaker.record_failure("NETWORK")

    # 100 → 200 → 400 → 400(상한 max_cooldown_seconds)
    assert observed == pytest.approx([100.0, 200.0, 400.0, 400.0], abs=1.5)


def test_success_resets_failures_and_backoff(breaker, fake_clock):
    """로그인 성공은 연속 실패·백오프 단계를 모두 초기화한다."""
    for _ in range(3):
        breaker.record_failure("NETWORK")
    fake_clock.advance(101.0)
    assert breaker.allow() is True

    breaker.record_success()
    status = breaker.status()
    assert status["blocked"] is False
    assert status["consecutive_failures"] == 0
    assert status["last_error_code"] == "CD001"

    # 백오프 단계도 초기화됐으므로 다시 base 쿨다운부터 시작한다.
    for _ in range(3):
        breaker.record_failure("NETWORK")
    assert breaker.status()["cooldown_remaining_seconds"] == pytest.approx(100.0, abs=1.0)


def test_describe_error_code_explains_known_codes():
    """진단을 가로막았던 KRX 원본 코드를 사람이 읽을 수 있게 남긴다."""
    assert describe_error_code("CD007") == "CD007(패스워드 오류수에 의한 잠금)"
    assert describe_error_code("CD010") == "CD010(패스워드 변경 필요)"
    assert describe_error_code("NETWORK") == "NETWORK"


# ── pykrx 로그인 진입점 가드 ──────────────────────────────────────────────────


@pytest.fixture
def guarded(monkeypatch, breaker):
    """전역 회로차단기를 테스트용으로 교체한다."""
    krx_auth.set_breaker(breaker)
    yield breaker
    krx_auth.set_breaker(None)


def test_open_circuit_sends_no_http_request(guarded, monkeypatch):
    """차단 중에는 로그인 HTTP 요청 자체가 나가지 않는다.

    KRX 쪽 실패 카운터가 더 쌓이지 않아야 잠금이 풀린 뒤 복구가 가능하다.
    """
    calls: list[tuple[str, str]] = []

    def _never(login_id, login_pw, session=None):
        calls.append((login_id, login_pw))
        raise AssertionError("차단 중에는 로그인 요청을 보내면 안 된다")

    monkeypatch.setattr(krx_auth, "_login_with_error_code", _never)
    guarded.record_failure("CD007")

    assert krx_auth._guarded_login_krx("jack68", "pw") is False
    assert calls == []


def test_guarded_login_records_krx_error_code(guarded, monkeypatch):
    """실패 시 KRX 원본 코드가 회로차단기에 기록된다."""
    monkeypatch.setattr(
        krx_auth,
        "_login_with_error_code",
        lambda *a, **k: (False, "CD010", "패스워드 변경 필요"),
    )

    assert krx_auth._guarded_login_krx("jack68", "expired") is False
    assert guarded.status()["last_error_code"] == "CD010"
    assert guarded.status()["blocked"] is True


def test_guarded_login_treats_exception_as_transient(guarded, monkeypatch):
    """네트워크 예외는 일시 실패로 처리한다 (즉시 차단하지 않는다)."""

    def _boom(*a, **k):
        raise ConnectionError("connection reset")

    monkeypatch.setattr(krx_auth, "_login_with_error_code", _boom)

    assert krx_auth._guarded_login_krx("jack68", "pw") is False
    status = guarded.status()
    assert status["last_error_code"] == "NETWORK"
    assert status["blocked"] is False


def test_guarded_login_success_resets_breaker(guarded, monkeypatch):
    monkeypatch.setattr(
        krx_auth, "_login_with_error_code", lambda *a, **k: (True, "CD001", "")
    )
    guarded.record_failure("NETWORK")

    assert krx_auth._guarded_login_krx("jack68", "pw") is True
    assert guarded.status()["consecutive_failures"] == 0


# ── 가드 설치 (pykrx 모듈 mock) ───────────────────────────────────────────────


@pytest.fixture
def fake_pykrx_auth(monkeypatch):
    """네트워크를 타지 않는 가짜 pykrx 인증 모듈을 sys.modules 에 심는다."""
    auth = types.ModuleType("pykrx.website.comm.auth")
    auth.USER_AGENT = "test-agent"
    auth.LOGIN_PAGE = "https://example.invalid/login"
    auth.LOGIN_URL = "https://example.invalid/login.cmd"
    auth.warmup_krx_session = lambda session: None
    auth.login_krx = lambda login_id, login_pw, session=None: False
    auth.build_krx_session = lambda login_id=None, login_pw=None: None
    auth._auth_session = None

    pykrx = types.ModuleType("pykrx")
    website = types.ModuleType("pykrx.website")
    comm = types.ModuleType("pykrx.website.comm")
    comm.auth = auth
    website.comm = comm
    pykrx.website = website

    for name, module in {
        "pykrx": pykrx,
        "pykrx.website": website,
        "pykrx.website.comm": comm,
        "pykrx.website.comm.auth": auth,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)

    monkeypatch.setattr(krx_auth, "_installed", False)
    monkeypatch.setattr(krx_auth, "_original_build_krx_session", None)
    yield auth
    monkeypatch.setattr(krx_auth, "_installed", False)


def test_install_replaces_pykrx_login_entrypoints(fake_pykrx_auth, guarded, monkeypatch):
    """설치 후에는 pykrx 의 로그인 진입점이 가드 함수로 바뀐다."""
    monkeypatch.delenv("KRX_ID", raising=False)
    monkeypatch.delenv("KRX_PW", raising=False)

    assert krx_auth.install_krx_login_guard(force=True) is True
    assert fake_pykrx_auth.login_krx is krx_auth._guarded_login_krx
    assert fake_pykrx_auth.build_krx_session is krx_auth._guarded_build_krx_session
    assert krx_auth.krx_login_status()["guard_installed"] is True


def test_install_is_idempotent(fake_pykrx_auth, guarded, monkeypatch):
    """반복 설치해도 원본 함수를 잃지 않는다 (가드가 가드를 감싸지 않는다)."""
    monkeypatch.delenv("KRX_ID", raising=False)
    monkeypatch.delenv("KRX_PW", raising=False)
    original = fake_pykrx_auth.build_krx_session

    krx_auth.install_krx_login_guard(force=True)
    krx_auth.install_krx_login_guard(force=True)

    assert krx_auth._original_build_krx_session is original


def test_install_records_import_time_login_failure(fake_pykrx_auth, guarded, monkeypatch):
    """임포트 시점 로그인 실패(가드보다 먼저 일어남)를 회로에 반영한다."""
    monkeypatch.setenv("KRX_ID", "jack68")
    monkeypatch.setenv("KRX_PW", "expired")
    fake_pykrx_auth._auth_session = None

    krx_auth.install_krx_login_guard(force=True)

    assert guarded.status()["consecutive_failures"] == 1
    assert guarded.status()["last_error_code"] == "IMPORT"


def test_install_skips_when_pykrx_api_changed(fake_pykrx_auth, guarded, monkeypatch):
    """pykrx 인증 API가 바뀌면 설치를 포기하되 예외를 던지지 않는다."""
    del fake_pykrx_auth.LOGIN_URL

    assert krx_auth.install_krx_login_guard(force=True) is False


def test_ensure_guard_swallows_install_errors(monkeypatch):
    """가드 설치 실패가 데이터 수집 경로를 깨뜨리지 않는다."""

    def _boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(krx_auth, "install_krx_login_guard", _boom)
    assert krx_auth.ensure_krx_login_guard() is False


def test_guard_disabled_by_setting(fake_pykrx_auth, guarded, monkeypatch):
    """MAPS_KRX_LOGIN_GUARD_ENABLED=false 이면 설치하지 않는다."""
    monkeypatch.setenv("MAPS_KRX_LOGIN_GUARD_ENABLED", "false")
    from maps.common.settings import reload_settings

    reload_settings()
    try:
        assert krx_auth.install_krx_login_guard(force=True) is False
        assert fake_pykrx_auth.login_krx is not krx_auth._guarded_login_krx
    finally:
        monkeypatch.delenv("MAPS_KRX_LOGIN_GUARD_ENABLED", raising=False)
        reload_settings()


# ── 로그인 응답 파싱 ──────────────────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class _FakeSession:
    """POST 응답을 순서대로 돌려주는 가짜 requests.Session."""

    def __init__(self, payloads: list[dict]) -> None:
        self._payloads = list(payloads)
        self.posts: list[dict] = []

    def post(self, url, data=None, headers=None, timeout=None):
        self.posts.append(dict(data or {}))
        return _FakeResponse(self._payloads.pop(0))


def test_login_parses_error_code(fake_pykrx_auth):
    """pykrx 가 bool 로 삼키는 _error_code 를 그대로 꺼내온다."""
    session = _FakeSession([{"_error_code": "CD007", "_error_message": "잠금"}])

    ok, code, message = krx_auth._login_with_error_code("jack68", "pw", session)

    assert (ok, code, message) == (False, "CD007", "잠금")


def test_login_retries_duplicate_session_with_skipdup(fake_pykrx_auth):
    """CD011(중복 로그인)은 skipDup 재전송이 정상 절차 — 실패로 세지 않는다."""
    session = _FakeSession(
        [
            {"_error_code": "CD011", "_error_message": "중복 로그인"},
            {"_error_code": "CD001", "_error_message": ""},
        ]
    )

    ok, code, _ = krx_auth._login_with_error_code("jack68", "pw", session)

    assert (ok, code) == (True, "CD001")
    assert len(session.posts) == 2
    assert session.posts[1]["skipDup"] == "Y"


def test_build_session_skipped_while_circuit_open(fake_pykrx_auth, guarded, monkeypatch):
    """차단 중에는 세션 생성(warmup 2회 GET 포함)도 건너뛴다."""
    monkeypatch.setenv("KRX_ID", "jack68")
    monkeypatch.setenv("KRX_PW", "pw")
    called: list[str] = []

    monkeypatch.setattr(
        krx_auth, "_original_build_krx_session", lambda *a: called.append("built")
    )
    guarded.record_failure("CD007")

    assert krx_auth._guarded_build_krx_session() is None
    assert called == []
