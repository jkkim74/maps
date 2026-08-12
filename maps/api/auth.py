"""계정 기반 로그인 — 세션 쿠키 인증 + 역할 게이트.

운영(`MAPS_AUTH_ENABLED=true`)에서만 동작하며, 모든 HTML 페이지와 `/api/*`를
`app_user` 계정으로 보호한다. 인증·권한 로직은 main.py에 등록되는 단일 게이트
미들웨어(`auth_gate_middleware`)와 로그인/로그아웃 라우터로 구성된다.

권한은 **허용 목록(allowlist)** 이다. `_USER_ALLOWED` 에 없는 경로는 전부 관리자
전용이므로, 새 라우터를 추가해도 일반 사용자에게 실수로 열리지 않는다.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from maps.common import db as db_module
from maps.common.models import AppUser
from maps.common.passwords import hash_password, needs_rehash, verify_password
from maps.common.settings import MapsSettings, get_settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])
templates = Jinja2Templates(directory="templates")

SESSION_USER_KEY = "user"

ROLE_ADMIN = "admin"
ROLE_USER = "user"
STATUS_ACTIVE = "active"

# 모바일(Capacitor) 앱은 다른 오리진이라 세션 쿠키(SameSite=Lax)를 보낼 수 없다.
# 대신 itsdangerous 서명 토큰을 발급하고 `Authorization: Bearer <token>` 로 인증한다.
_MOBILE_TOKEN_SALT = "maps-mobile-auth"


@dataclass(frozen=True)
class Identity:
    """현재 요청을 수행하는 주체. `request.state.user` 에 실린다."""

    id: int | None
    username: str
    role: str

    @property
    def is_admin(self) -> bool:
        """관리자 권한 여부. 인증 비활성(개발·테스트)도 관리자로 본다."""
        return self.role == ROLE_ADMIN


# 인증이 꺼진 환경(로컬·테스트)에서 쓰는 주체. 기존 단일 사용자 동작을 그대로 유지한다.
ANONYMOUS_ADMIN = Identity(id=None, username="local", role=ROLE_ADMIN)


def _token_serializer(settings: MapsSettings) -> URLSafeTimedSerializer:
    """세션 서명 키를 재사용하는 토큰 직렬화기."""
    secret = settings.maps_session_secret_key or "maps-insecure-dev-secret"
    return URLSafeTimedSerializer(secret, salt=_MOBILE_TOKEN_SALT)


def make_mobile_token(username: str, settings: MapsSettings | None = None) -> str:
    """모바일 앱용 서명 토큰을 발급한다.

    토큰에는 username 만 담는다. 역할·상태는 매 요청 DB에서 조회하므로 계정을
    비활성화하면 이미 발급된 토큰도 즉시 막힌다.
    """
    settings = settings or get_settings()
    return _token_serializer(settings).dumps({"user": username})


def verify_mobile_token(token: str, settings: MapsSettings | None = None) -> str | None:
    """토큰을 검증해 username을 반환한다. 만료/위조면 None."""
    settings = settings or get_settings()
    try:
        data = _token_serializer(settings).loads(token, max_age=settings.maps_session_max_age)
    except (BadSignature, SignatureExpired):
        return None
    user = data.get("user") if isinstance(data, dict) else None
    return user or None


def _bearer_token(request: Request) -> str | None:
    """Authorization: Bearer <token> 헤더에서 토큰을 추출한다."""
    header = request.headers.get("Authorization", "")
    prefix = "Bearer "
    if header.startswith(prefix):
        return header[len(prefix):].strip() or None
    return None


# 인증 없이 접근 가능한 경로(접두사 포함).
_PUBLIC_PATHS: frozenset[str] = frozenset(
    {"/login", "/logout", "/health", "/favicon.ico",
     "/api/telegram/webhook", "/api/v1/mobile/login"}
)
_PUBLIC_PREFIXES: tuple[str, ...] = ("/static/",)

# role=user 에게 허용되는 경로와 메서드. 여기 없으면 전부 관리자 전용이다.
# 상태를 바꾸는 동작은 모두 POST/PUT/DELETE 이므로 GET 만 여는 것으로 조회 전용이 된다.
_USER_ALLOWED: dict[str, frozenset[str]] = {
    "/": frozenset({"GET"}),
    "/stock-analysis": frozenset({"GET"}),
    "/candidates": frozenset({"GET"}),
    "/market": frozenset({"GET"}),
    "/analysis-picks": frozenset({"GET"}),
    "/maps-intro": frozenset({"GET"}),
    "/blog": frozenset({"GET"}),
    "/settings": frozenset({"GET"}),
    "/api/v1/stock-analysis": frozenset({"GET", "POST"}),  # 분석 실행 — 일일 한도 적용
    "/api/v1/candidates": frozenset({"GET"}),
    "/api/v1/market": frozenset({"GET"}),
    "/api/v1/analysis-picks": frozenset({"GET"}),  # 무장·중지는 POST → 차단
    "/api/v1/blog": frozenset({"GET"}),
    "/api/v1/users/me": frozenset({"GET", "PUT", "POST"}),
}


def is_public_path(path: str) -> bool:
    """인증이 필요 없는 공개 경로인지 판단한다."""
    if path in _PUBLIC_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in _PUBLIC_PREFIXES)


def is_allowed(role: str, path: str, method: str) -> bool:
    """역할이 이 경로·메서드를 쓸 수 있는지 판단한다 (fail-closed)."""
    if role == ROLE_ADMIN:
        return True
    for prefix, methods in _USER_ALLOWED.items():
        if path == prefix or (prefix != "/" and path.startswith(prefix + "/")):
            return method.upper() in methods
    return False


def authenticate(db: Session, username: str, password: str) -> AppUser | None:
    """자격증명을 검증하고 활성 계정을 반환한다. 실패하면 None.

    비밀번호 파라미터가 낡았으면 로그인 성공 시점에 조용히 재해시한다.
    """
    if not username or not password:
        return None
    user = db.query(AppUser).filter(AppUser.username == username).one_or_none()
    if user is None or user.status != STATUS_ACTIVE:
        return None
    if not verify_password(password, user.password_hash):
        return None
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    user.last_login_at = dt.datetime.now(dt.timezone.utc)
    db.commit()
    return user


def load_user(db: Session, username: str) -> AppUser | None:
    """활성 계정을 username 으로 조회한다."""
    if not username:
        return None
    user = db.query(AppUser).filter(AppUser.username == username).one_or_none()
    return user if user is not None and user.status == STATUS_ACTIVE else None


def ensure_bootstrap_admin(settings: MapsSettings | None = None) -> None:
    """계정이 하나도 없을 때만 `.env` 자격증명으로 관리자 1명을 만든다.

    이후 로그인은 항상 DB를 본다. `.env` 비밀번호를 인증 경로에 남겨 두면 계정을
    비활성화해도 뒷문이 열려 있게 되므로, 시드는 최초 1회로 끝낸다.
    """
    settings = settings or get_settings()
    if not settings.maps_auth_password:
        return
    db = db_module.SessionLocal()
    try:
        if db.query(AppUser.id).first() is not None:
            return
        db.add(
            AppUser(
                username=settings.maps_auth_username,
                display_name="운영자",
                password_hash=hash_password(settings.maps_auth_password),
                role=ROLE_ADMIN,
                status=STATUS_ACTIVE,
            )
        )
        db.commit()
        logger.info("부트스트랩 관리자 계정 생성: %s", settings.maps_auth_username)
    except Exception:  # noqa: BLE001 — 기동을 막지 않는다. 로그인 시 다시 시도된다.
        db.rollback()
        logger.exception("부트스트랩 관리자 계정 생성 실패")
    finally:
        db.close()


def current_identity(request: Request) -> Identity:
    """요청 주체를 반환한다. 인증이 꺼져 있으면 관리자로 본다."""
    identity = getattr(request.state, "user", None)
    return identity if isinstance(identity, Identity) else ANONYMOUS_ADMIN


def _session_username(request: Request) -> str | None:
    """세션 쿠키에 담긴 username."""
    try:
        return request.session.get(SESSION_USER_KEY)
    except AssertionError:
        # SessionMiddleware가 스택에 없는 경우(이론상 발생하지 않음)
        return None


def is_authenticated(request: Request) -> bool:
    """현재 요청에 유효한 로그인 세션이 있는지 반환한다."""
    return bool(_session_username(request))


def _resolve_identity(request: Request) -> Identity | None:
    """세션 또는 Bearer 토큰에서 활성 계정을 찾는다."""
    username = _session_username(request)
    if not username:
        token = _bearer_token(request)
        username = verify_mobile_token(token) if token else None
    if not username:
        return None

    db = db_module.SessionLocal()
    try:
        user = load_user(db, username)
        if user is None:
            return None
        return Identity(id=user.id, username=user.username, role=user.role)
    finally:
        db.close()


def _deny(request: Request, status: int) -> Response:
    """API는 상태 코드, 화면은 로그인/거부 안내로 응답한다."""
    path = request.url.path
    if path.startswith("/api/"):
        detail = "Not authenticated" if status == 401 else "권한이 없습니다."
        return JSONResponse({"detail": detail}, status_code=status)
    if status == 401:
        return RedirectResponse(url=f"/login?next={_safe_next(path)}", status_code=303)
    # base.html 이 기대하는 최소 컨텍스트만 채운다(네비게이션은 비운다).
    return templates.TemplateResponse(
        request,
        "forbidden.html",
        {"title": "권한 없음", "screen": "", "nav_keys": set(),
         "nav_items": [], "current_user": None, "static_ver": ""},
        status_code=403,
    )


async def auth_gate_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """인증·권한 게이트 — 비활성 시 통과, 활성 시 계정과 역할을 요구한다."""
    settings = get_settings()
    if not settings.maps_auth_enabled:
        request.state.user = ANONYMOUS_ADMIN
        return await call_next(request)

    path = request.url.path
    if is_public_path(path):
        request.state.user = None
        return await call_next(request)

    identity = _resolve_identity(request)
    if identity is None:
        return _deny(request, 401)

    if not is_allowed(identity.role, path, request.method):
        logger.info("권한 거부: user=%s role=%s %s %s",
                    identity.username, identity.role, request.method, path)
        return _deny(request, 403)

    request.state.user = identity
    return await call_next(request)


def _safe_next(target: str | None) -> str:
    """오픈 리다이렉트 방지: 내부 경로(/...)만 허용한다."""
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return "/"


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str = "/") -> Response:
    """로그인 폼. 이미 로그인돼 있으면 next로 리다이렉트."""
    if is_authenticated(request):
        return RedirectResponse(url=_safe_next(next), status_code=303)
    return templates.TemplateResponse(
        request, "login.html", {"error": None, "next": _safe_next(next)}
    )


@router.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(default=""),
    password: str = Form(default=""),
    next: str = Form(default="/"),
) -> Response:
    """자격증명을 DB로 검증한 뒤 세션을 설정한다."""
    db = db_module.SessionLocal()
    try:
        user = authenticate(db, username, password)
        landing = _landing_path(user) if user is not None else "/"
    finally:
        db.close()

    if user is None:
        logger.warning("로그인 실패 (username=%s)", username or "<blank>")
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "아이디 또는 비밀번호가 올바르지 않습니다.", "next": _safe_next(next)},
            status_code=401,
        )
    request.session[SESSION_USER_KEY] = user.username
    target = _safe_next(next)
    if target == "/" and landing != "/":
        target = landing
    return RedirectResponse(url=target, status_code=303)


def _landing_path(user: AppUser) -> str:
    """개인 설정의 시작 화면 경로. 설정이 없으면 기본 `/`."""
    screen = (user.preferences or {}).get("landing_screen") if user.preferences else None
    return f"/{screen}" if screen else "/"


@router.get("/logout")
async def logout(request: Request) -> Response:
    """세션을 비우고 로그인 화면으로 보낸다."""
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)
