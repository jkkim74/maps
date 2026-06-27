"""단일 공용 비밀번호 기반 로그인 — 세션 쿠키 인증.

운영(`MAPS_AUTH_ENABLED=true`)에서만 동작하며, 모든 HTML 페이지와 `/api/*`를
세션 쿠키로 보호한다. 인증 로직은 main.py에 등록되는 단일 게이트 미들웨어
(`auth_gate_middleware`)와 로그인/로그아웃 라우터로 구성된다.
"""

from __future__ import annotations

import logging
import secrets
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from maps.common.settings import MapsSettings, get_settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])
templates = Jinja2Templates(directory="templates")

SESSION_USER_KEY = "user"

# 인증 없이 접근 가능한 경로(접두사 포함).
_PUBLIC_PATHS: frozenset[str] = frozenset({"/login", "/logout", "/health", "/favicon.ico"})
_PUBLIC_PREFIXES: tuple[str, ...] = ("/static/",)


def is_public_path(path: str) -> bool:
    """인증이 필요 없는 공개 경로인지 판단한다."""
    if path in _PUBLIC_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in _PUBLIC_PREFIXES)


def is_authenticated(request: Request) -> bool:
    """현재 요청에 유효한 로그인 세션이 있는지 반환한다."""
    try:
        return bool(request.session.get(SESSION_USER_KEY))
    except AssertionError:
        # SessionMiddleware가 스택에 없는 경우(이론상 발생하지 않음)
        return False


def check_credentials(settings: MapsSettings, username: str, password: str) -> bool:
    """상수 시간 비교로 자격증명을 검증한다."""
    expected_pw = settings.maps_auth_password
    if not expected_pw:
        return False  # 비밀번호 미설정 → 모든 로그인 거부(fail-closed)
    pw_ok = secrets.compare_digest(password or "", expected_pw)
    user_ok = secrets.compare_digest(username or "", settings.maps_auth_username)
    return pw_ok and user_ok


def _safe_next(target: str | None) -> str:
    """오픈 리다이렉트 방지: 내부 경로(/...)만 허용한다."""
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return "/"


async def auth_gate_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """인증 게이트 — 비활성 시 통과, 활성 시 세션을 요구한다."""
    settings = get_settings()
    if not settings.maps_auth_enabled:
        return await call_next(request)

    path = request.url.path
    if is_public_path(path) or is_authenticated(request):
        return await call_next(request)

    # 미인증: API는 401, 화면은 로그인으로 리다이렉트
    if path.startswith("/api/"):
        return JSONResponse({"detail": "Not authenticated"}, status_code=401)
    return RedirectResponse(url=f"/login?next={_safe_next(path)}", status_code=303)


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
    """자격증명 검증 후 세션을 설정한다."""
    settings = get_settings()
    if not check_credentials(settings, username, password):
        logger.warning("로그인 실패 (username=%s)", username or "<blank>")
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "아이디 또는 비밀번호가 올바르지 않습니다.", "next": _safe_next(next)},
            status_code=401,
        )
    request.session[SESSION_USER_KEY] = username or settings.maps_auth_username
    return RedirectResponse(url=_safe_next(next), status_code=303)


@router.get("/logout")
async def logout(request: Request) -> Response:
    """세션을 비우고 로그인 화면으로 보낸다."""
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)
