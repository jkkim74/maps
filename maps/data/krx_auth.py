"""KRX 로그인 회로차단기(circuit breaker).

pykrx 는 데이터 요청마다 ``get_auth_session()`` 을 호출하고, 인증 세션이 없으면
그 자리에서 재로그인을 시도한다. 자격증명이 만료된 상태(``CD010``)에서는 이
재시도가 **매 요청마다 무한정** 반복되고, 누적된 로그인 실패가 KRX 계정을
오류횟수 초과로 잠근다(``CD007``). 2026-07-27 운영에서 실제로 하루 158 회
재시도가 계정 잠금을 유발했고, 잠긴 뒤에는 올바른 비밀번호를 넣어도 ``CD007``
만 돌아와 "비밀번호가 틀렸다"로 오진하기 쉬웠다.

이 모듈은 pykrx(벤더 코드)의 로그인 진입점을 감싸 다음을 보장한다.

1. **치명 코드는 즉시 차단** — ``CD007``(잠금) / ``CD010``(변경 필요) 는 재시도로
   회복되지 않는다. 오히려 재시도가 잠금을 유발하거나 연장하므로 1 회 실패로
   곧장 회로를 연다.
2. **일시 오류는 N 회 연속 실패 후 차단** — 네트워크 오류 등은
   ``maps_krx_login_max_failures`` 회 연속 실패 시 회로를 열고, 회로가 열릴
   때마다 쿨다운을 2 배로 늘린다(상한 ``maps_krx_login_max_cooldown_seconds``).
3. **회로가 열린 동안에는 HTTP 요청 자체를 보내지 않는다.** KRX 쪽에 실패가
   더 쌓이지 않고, 상위 코드는 기존 폴백(장세 판정의 yfinance 등)으로 계속
   동작한다.

또한 pykrx 의 ``login_krx()`` 가 bool 로 뭉개 삼키는 KRX 원본 오류 코드를
그대로 로그에 남긴다 — 지난 사고에서 진단을 가로막은 지점이다.

.. note::
   ``pykrx.website.comm.webio`` 는 **임포트 시점에** 로그인을 1 회 시도한다.
   가드는 그보다 앞설 수 없으므로 프로세스당 1 회의 시도는 남는다.
   :func:`install_krx_login_guard` 는 그 결과를 확인해 회로 상태에 반영한다.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# KRX 로그인 응답 코드. pykrx ``login_krx()`` 는 이 값을 bool 로 뭉개 버린다.
KRX_ERROR_MESSAGES: dict[str, str] = {
    "CD001": "정상",
    "CD007": "패스워드 오류수에 의한 잠금",
    "CD010": "패스워드 변경 필요",
    "CD011": "중복 로그인",
}

#: 재시도로 회복될 수 없는 코드. 재시도가 오히려 잠금을 유발/연장한다.
TERMINAL_ERROR_CODES: frozenset[str] = frozenset({"CD007", "CD010"})

#: 로그인 요청 타임아웃(초). pykrx 원본과 동일하게 맞춘다.
_LOGIN_TIMEOUT = 15


def describe_error_code(code: str) -> str:
    """KRX 오류 코드를 사람이 읽을 수 있는 문자열로 변환한다.

    :param code: KRX ``_error_code`` 값 또는 내부 의사 코드.
    :return: ``"CD007(패스워드 오류수에 의한 잠금)"`` 형태의 설명 문자열.
    """
    known = KRX_ERROR_MESSAGES.get(code)
    return f"{code}({known})" if known else code


@dataclass
class KRXLoginBreaker:
    """KRX 로그인 실패에 대한 연속 실패 카운터 + 지수 백오프 회로차단기.

    스케줄러 잡과 API 요청이 같은 프로세스에서 동시에 pykrx 를 건드리므로
    모든 상태 전이는 락으로 보호한다. 쿨다운 계산에는 시스템 시각 변경의
    영향을 받지 않도록 :func:`time.monotonic` 을 쓴다.

    :param max_failures: 회로를 열기까지 허용하는 연속 실패 횟수(일시 오류 기준).
    :param base_cooldown_seconds: 최초 차단 시간(초).
    :param max_cooldown_seconds: 지수 백오프 상한(초). 치명 코드는 즉시 이 값을 쓴다.
    """

    max_failures: int = 3
    base_cooldown_seconds: float = 1800.0
    max_cooldown_seconds: float = 21600.0

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _consecutive_failures: int = 0
    _open_count: int = 0
    _open_until: float = 0.0
    _suppressed_attempts: int = 0
    _last_error_code: str | None = None

    def allow(self) -> bool:
        """지금 로그인을 시도해도 되는지 판단한다.

        회로가 열려 있으면 억제 횟수만 세고 ``False`` 를 반환한다 — 호출자는
        HTTP 요청을 보내지 않아야 한다.

        :return: 로그인 시도 허용 여부.
        """
        with self._lock:
            if self._open_until <= 0.0:
                return True

            remaining = self._open_until - time.monotonic()
            if remaining > 0:
                self._suppressed_attempts += 1
                # 억제 로그는 처음과 이후 배수 지점에서만 남긴다(로그 폭주 방지).
                if self._suppressed_attempts == 1 or self._suppressed_attempts % 50 == 0:
                    logger.warning(
                        "KRX 로그인 차단 중 — %.0f초 남음, 억제된 시도 %d회, 최근 코드 %s. "
                        "차단 동안에는 폴백 데이터 소스로 동작합니다.",
                        remaining,
                        self._suppressed_attempts,
                        describe_error_code(self._last_error_code or "UNKNOWN"),
                    )
                return False

            logger.info(
                "KRX 로그인 차단 해제 — 쿨다운 종료(억제된 시도 %d회). 재시도를 1회 허용합니다.",
                self._suppressed_attempts,
            )
            self._open_until = 0.0
            self._suppressed_attempts = 0
            return True

    def record_success(self) -> None:
        """로그인 성공을 기록하고 회로를 완전히 초기화한다."""
        with self._lock:
            recovered = self._consecutive_failures > 0 or self._open_count > 0
            self._consecutive_failures = 0
            self._open_count = 0
            self._open_until = 0.0
            self._suppressed_attempts = 0
            self._last_error_code = "CD001"
        if recovered:
            logger.info("KRX 로그인 성공 — 회로차단기 초기화")

    def record_failure(self, error_code: str, detail: str = "") -> None:
        """로그인 실패를 기록하고 필요하면 회로를 연다.

        :param error_code: KRX ``_error_code`` 또는 내부 의사 코드
            (``NETWORK`` / ``IMPORT`` / ``UNKNOWN``).
        :param detail: 로그에 함께 남길 원본 메시지.
        """
        code = (error_code or "UNKNOWN").strip() or "UNKNOWN"
        terminal = code in TERMINAL_ERROR_CODES

        with self._lock:
            self._consecutive_failures += 1
            self._last_error_code = code
            failures = self._consecutive_failures
            should_open = terminal or failures >= self.max_failures

            if should_open:
                self._open_count += 1
                if terminal:
                    # 자격증명 문제는 사람이 고치기 전까지 회복 불가 — 최장 쿨다운.
                    cooldown = self.max_cooldown_seconds
                else:
                    cooldown = min(
                        self.base_cooldown_seconds * (2 ** (self._open_count - 1)),
                        self.max_cooldown_seconds,
                    )
                self._open_until = time.monotonic() + cooldown
                self._suppressed_attempts = 0
            else:
                cooldown = 0.0

        if not should_open:
            logger.warning(
                "KRX 로그인 실패 %d/%d — 코드 %s %s",
                failures,
                self.max_failures,
                describe_error_code(code),
                detail,
            )
            return

        if terminal:
            logger.error(
                "KRX 로그인 차단 — 코드 %s 는 재시도로 회복되지 않습니다. %.0f초 동안 "
                "로그인 시도를 중단합니다(재시도 누적이 계정 잠금을 유발합니다). "
                "서버 .env 의 KRX_ID/KRX_PW 를 점검하고 krx.co.kr 에서 잠금을 해제하세요. %s",
                describe_error_code(code),
                cooldown,
                detail,
            )
        else:
            logger.error(
                "KRX 로그인 차단 — %d회 연속 실패(최근 코드 %s). %.0f초 동안 로그인 시도를 "
                "중단합니다. %s",
                failures,
                describe_error_code(code),
                cooldown,
                detail,
            )

    def status(self) -> dict[str, object]:
        """관측용 현재 상태 스냅샷을 반환한다.

        :return: 차단 여부·남은 시간·연속 실패 수 등을 담은 딕셔너리.
        """
        with self._lock:
            remaining = max(0.0, self._open_until - time.monotonic())
            return {
                "blocked": remaining > 0,
                "cooldown_remaining_seconds": round(remaining, 1),
                "consecutive_failures": self._consecutive_failures,
                "open_count": self._open_count,
                "suppressed_attempts": self._suppressed_attempts,
                "last_error_code": self._last_error_code,
            }

    def reset(self) -> None:
        """모든 상태를 초기화한다 (테스트·수동 복구용)."""
        with self._lock:
            self._consecutive_failures = 0
            self._open_count = 0
            self._open_until = 0.0
            self._suppressed_attempts = 0
            self._last_error_code = None


_breaker: KRXLoginBreaker | None = None
_breaker_lock = threading.Lock()


def get_breaker() -> KRXLoginBreaker:
    """프로세스 전역 회로차단기를 반환한다 (최초 호출 시 설정으로 생성).

    :return: 전역 :class:`KRXLoginBreaker` 인스턴스.
    """
    global _breaker
    with _breaker_lock:
        if _breaker is None:
            from maps.common.settings import get_settings  # noqa: PLC0415 — 순환 임포트 방지

            settings = get_settings()
            _breaker = KRXLoginBreaker(
                max_failures=settings.maps_krx_login_max_failures,
                base_cooldown_seconds=float(settings.maps_krx_login_cooldown_seconds),
                max_cooldown_seconds=float(settings.maps_krx_login_max_cooldown_seconds),
            )
        return _breaker


def set_breaker(breaker: KRXLoginBreaker | None) -> None:
    """전역 회로차단기를 교체한다 (테스트 전용).

    :param breaker: 새 인스턴스. ``None`` 이면 다음 호출에서 설정 기반으로 재생성된다.
    """
    global _breaker
    with _breaker_lock:
        _breaker = breaker


def _login_with_error_code(
    login_id: str, login_pw: str, session=None
) -> tuple[bool, str, str]:
    """pykrx ``login_krx()`` 를 재현하되 KRX 원본 오류 코드를 함께 반환한다.

    원본은 성공 여부만 bool 로 돌려주기 때문에 ``CD007``(잠금)과 ``CD010``
    (변경 필요)을 구분할 수 없다. URL·헤더는 pykrx 모듈에서 그대로 가져와
    벤더 변경을 자동으로 따라간다.

    :param login_id: KRX 로그인 ID.
    :param login_pw: KRX 로그인 비밀번호.
    :param session: 재사용할 ``requests.Session``. 없으면 새로 만든다.
    :return: ``(성공 여부, 오류 코드, 오류 메시지)``.
    """
    import requests  # noqa: PLC0415
    from pykrx.website.comm import auth as _auth  # noqa: PLC0415

    if session is None:
        session = requests.Session()

    _auth.warmup_krx_session(session)

    payload = {
        "mbrNm": "",
        "telNo": "",
        "di": "",
        "certType": "",
        "mbrId": login_id,
        "pw": login_pw,
    }
    headers = {"User-Agent": _auth.USER_AGENT, "Referer": _auth.LOGIN_PAGE}

    resp = session.post(
        _auth.LOGIN_URL, data=payload, headers=headers, timeout=_LOGIN_TIMEOUT
    )
    data = resp.json()
    code = str(data.get("_error_code", "") or "")
    message = str(data.get("_error_message", "") or "")

    # CD011(중복 로그인)은 skipDup 재전송이 정상 절차다 — 실패로 세지 않는다.
    if code == "CD011":
        payload["skipDup"] = "Y"
        resp = session.post(
            _auth.LOGIN_URL, data=payload, headers=headers, timeout=_LOGIN_TIMEOUT
        )
        data = resp.json()
        code = str(data.get("_error_code", "") or "")
        message = str(data.get("_error_message", "") or "")

    return code == "CD001", code, message


def _guarded_login_krx(login_id: str, login_pw: str, session=None) -> bool:
    """회로차단기를 통과한 경우에만 실제 KRX 로그인을 수행한다.

    ``pykrx.website.comm.auth.login_krx`` 를 대체한다. 시그니처와 반환 타입은
    원본과 동일하다.

    :param login_id: KRX 로그인 ID.
    :param login_pw: KRX 로그인 비밀번호.
    :param session: 재사용할 ``requests.Session``.
    :return: 로그인 성공 여부. 회로가 열려 있으면 요청 없이 ``False``.
    """
    breaker = get_breaker()
    if not breaker.allow():
        return False

    try:
        ok, code, message = _login_with_error_code(login_id, login_pw, session)
    except Exception as exc:  # noqa: BLE001 — 네트워크/파싱 오류 모두 일시 실패로 처리
        breaker.record_failure("NETWORK", detail=f"({exc})")
        return False

    if ok:
        breaker.record_success()
    else:
        breaker.record_failure(code or "UNKNOWN", detail=f"({message})" if message else "")
    return ok


def _guarded_build_krx_session(login_id: str | None = None, login_pw: str | None = None):
    """회로가 열려 있으면 세션 생성(=로그인)을 건너뛴다.

    ``pykrx.website.comm.auth.build_krx_session`` 을 대체한다. 원본은 기본 인자를
    임포트 시점에 평가하지만 여기서는 호출 시점에 환경변수를 읽는다.
    ``KRX_ID``/``KRX_PW`` 는 MAPS 설정이 아니라 **pykrx 자체의 환경변수 계약**
    이므로 :mod:`maps.common.settings` 를 거치지 않는다.

    :param login_id: KRX 로그인 ID. 생략 시 ``KRX_ID`` 환경변수.
    :param login_pw: KRX 로그인 비밀번호. 생략 시 ``KRX_PW`` 환경변수.
    :return: 로그인에 성공하면 ``KRXSession``, 아니면 ``None``.
    """
    login_id = login_id or os.getenv("KRX_ID")
    login_pw = login_pw or os.getenv("KRX_PW")
    if not (login_id and login_pw):
        return None

    if not get_breaker().allow():
        return None

    if _original_build_krx_session is None:  # pragma: no cover — 설치 전에는 호출되지 않는다
        return None
    return _original_build_krx_session(login_id, login_pw)


_original_build_krx_session = None
_installed = False
_install_lock = threading.RLock()

#: 가드 설치 시점에 존재해야 하는 pykrx 인증 모듈 심볼.
_REQUIRED_AUTH_ATTRS = (
    "login_krx",
    "build_krx_session",
    "warmup_krx_session",
    "USER_AGENT",
    "LOGIN_URL",
    "LOGIN_PAGE",
)


def install_krx_login_guard(*, force: bool = False) -> bool:
    """pykrx 로그인 진입점에 회로차단기를 설치한다 (멱등).

    ``pykrx`` 를 실제로 쓰기 직전에 호출하면 된다. 두 번째 호출부터는 즉시
    반환하므로 호출 비용을 걱정할 필요가 없다. pykrx 인증 모듈의 구조가 바뀌어
    설치할 수 없으면 경고만 남기고 ``False`` 를 반환한다 — 데이터 수집 자체를
    막지는 않는다.

    :param force: 이미 설치되어 있어도 다시 설치한다 (테스트용).
    :return: 가드가 설치되어 있으면 ``True``.
    """
    global _installed, _original_build_krx_session

    with _install_lock:
        if _installed and not force:
            return True

        from maps.common.settings import get_settings  # noqa: PLC0415

        if not get_settings().maps_krx_login_guard_enabled:
            logger.warning(
                "KRX 로그인 회로차단기가 비활성(MAPS_KRX_LOGIN_GUARD_ENABLED=false) — "
                "자격증명 만료 시 재시도 누적으로 계정이 잠길 수 있습니다."
            )
            return False

        try:
            from pykrx.website.comm import auth as _auth  # noqa: PLC0415
        except ImportError as exc:
            logger.warning("KRX 로그인 회로차단기 설치 생략 — pykrx 인증 모듈 없음: %s", exc)
            return False

        missing = [name for name in _REQUIRED_AUTH_ATTRS if not hasattr(_auth, name)]
        if missing:
            logger.warning(
                "KRX 로그인 회로차단기 설치 생략 — pykrx auth API 변경(누락: %s)", missing
            )
            return False

        # 재설치(force) 시 이미 우리 함수로 바뀐 것을 원본으로 착각하지 않는다.
        if _auth.build_krx_session is not _guarded_build_krx_session:
            _original_build_krx_session = _auth.build_krx_session

        _auth.login_krx = _guarded_login_krx
        _auth.build_krx_session = _guarded_build_krx_session
        _installed = True

        # webio 는 임포트 시점에 로그인을 1회 시도한다 — 가드보다 먼저 일어나므로
        # 그 결과를 여기서 회로에 반영한다(실패했다면 이미 1회 실패한 상태다).
        if (
            getattr(_auth, "_auth_session", None) is None
            and os.getenv("KRX_ID")
            and os.getenv("KRX_PW")
        ):
            get_breaker().record_failure(
                "IMPORT", detail="(pykrx 임포트 시점 로그인 실패 — 코드 확인 불가)"
            )

        logger.info("KRX 로그인 회로차단기 설치 완료: %s", get_breaker().status())
        return True


def ensure_krx_login_guard() -> bool:
    """pykrx 사용 직전에 호출하는 얇은 래퍼 (설치 실패도 삼킨다).

    가드 설치 실패가 데이터 수집 경로를 깨뜨리면 안 되므로 예외를 밖으로
    내보내지 않는다.

    :return: 가드가 설치되어 있으면 ``True``.
    """
    try:
        return install_krx_login_guard()
    except Exception as exc:  # noqa: BLE001
        logger.warning("KRX 로그인 회로차단기 설치 실패: %s", exc)
        return False


def krx_login_status() -> dict[str, object]:
    """회로차단기 상태 + 설치 여부를 반환한다 (운영 관측용).

    :return: :meth:`KRXLoginBreaker.status` 결과에 ``guard_installed`` 를 더한 딕셔너리.
    """
    status = get_breaker().status()
    status["guard_installed"] = _installed
    return status
