"""회원 계정 API — 본인 설정과 관리자 회원 관리.

권한 판정은 라우터가 아니라 `maps/api/auth.py` 의 게이트 미들웨어가 한다.
여기서는 `/me` 계열이 **자기 자신만** 대상으로 하는지, 관리자 조작이 계정을
잠가 버리지 않는지 같은 도메인 규칙만 지킨다.
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.orm import Session

from maps.api.auth import ROLE_ADMIN, ROLE_USER, current_identity, load_user
from maps.api.deps import DbDep
from maps.api.schemas import (
    MeResponse,
    PasswordChangeRequest,
    PasswordResetResponse,
    UserCreateRequest,
    UserPreferences,
    UserSummary,
    UserUpdateRequest,
)
from maps.common.models import AppUser
from maps.common.passwords import hash_password, verify_password
from maps.common.user_prefs import (
    analysis_used_today,
    daily_analysis_limit,
    resolve,
)

router = APIRouter(prefix="/api/v1/users", tags=["Users"])

_ROLES = {ROLE_ADMIN, ROLE_USER}
_STATUSES = {"active", "pending", "disabled"}
_PLANS = {"free", "paid"}
_MIN_PASSWORD_LENGTH = 8


def _summary(user: AppUser) -> UserSummary:
    """ORM 계정을 응답 모델로 옮긴다. 비밀번호 해시는 옮기지 않는다."""
    return UserSummary(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        email=user.email,
        role=user.role,
        status=user.status,
        plan=user.plan,
        daily_analysis_limit=user.daily_analysis_limit,
        created_at=user.created_at.isoformat() if user.created_at else None,
        last_login_at=user.last_login_at.isoformat() if user.last_login_at else None,
    )


def _require_self(request: Request, db: Session) -> AppUser:
    """현재 로그인 계정을 반환한다. 인증이 꺼진 환경이면 404 대신 명시적 오류."""
    identity = current_identity(request)
    user = load_user(db, identity.username)
    if user is None:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    return user


@router.get("/me", response_model=MeResponse)
def get_me(request: Request, db: Session = DbDep) -> MeResponse:
    """내 계정과 해석된 개인 설정, 오늘 사용량을 반환한다."""
    user = _require_self(request, db)
    return MeResponse(
        user=_summary(user),
        preferences=resolve(user),
        analysis_used_today=analysis_used_today(db, user),
        analysis_limit=daily_analysis_limit(user),
    )


@router.put("/me/preferences", response_model=MeResponse)
def update_my_preferences(
    body: UserPreferences, request: Request, db: Session = DbDep
) -> MeResponse:
    """개인 설정을 검증 후 저장한다. 모르는 키는 스키마가 거부한다."""
    user = _require_self(request, db)
    user.preferences = body.model_dump()
    db.commit()
    db.refresh(user)
    return MeResponse(
        user=_summary(user),
        preferences=resolve(user),
        analysis_used_today=analysis_used_today(db, user),
        analysis_limit=daily_analysis_limit(user),
    )


@router.post("/me/password", response_model=UserSummary)
def change_my_password(
    body: PasswordChangeRequest, request: Request, db: Session = DbDep
) -> UserSummary:
    """현재 비밀번호를 확인한 뒤 새 비밀번호로 바꾼다."""
    user = _require_self(request, db)
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="현재 비밀번호가 올바르지 않습니다.")
    if len(body.new_password or "") < _MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=400, detail=f"새 비밀번호는 {_MIN_PASSWORD_LENGTH}자 이상이어야 합니다."
        )
    user.password_hash = hash_password(body.new_password)
    db.commit()
    return _summary(user)


# ── 관리자 전용 (게이트가 role=user 를 이미 차단한다) ─────────────────────────
@router.get("", response_model=list[UserSummary])
def list_users(db: Session = DbDep) -> list[UserSummary]:
    """회원 목록을 최신 가입순으로 반환한다."""
    users = db.query(AppUser).order_by(AppUser.id.desc()).all()
    return [_summary(user) for user in users]


@router.post("", response_model=UserSummary, status_code=201)
def create_user(body: UserCreateRequest, db: Session = DbDep) -> UserSummary:
    """관리자가 계정을 만든다(초대). 같은 username 은 만들 수 없다."""
    username = (body.username or "").strip()
    if not username:
        raise HTTPException(status_code=400, detail="아이디를 입력하세요.")
    if len(body.password or "") < _MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=400, detail=f"비밀번호는 {_MIN_PASSWORD_LENGTH}자 이상이어야 합니다."
        )
    if body.role not in _ROLES:
        raise HTTPException(status_code=400, detail=f"role 은 {sorted(_ROLES)} 중 하나여야 합니다.")
    if db.query(AppUser).filter(AppUser.username == username).first() is not None:
        raise HTTPException(status_code=409, detail="이미 존재하는 아이디입니다.")

    user = AppUser(
        username=username,
        display_name=body.display_name,
        email=body.email,
        password_hash=hash_password(body.password),
        role=body.role,
        status="active",
        daily_analysis_limit=body.daily_analysis_limit,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return _summary(user)


@router.put("/{user_id}", response_model=UserSummary)
def update_user(
    user_id: int, body: UserUpdateRequest, request: Request, db: Session = DbDep
) -> UserSummary:
    """역할·상태·요금제·한도를 변경한다."""
    user = db.query(AppUser).filter(AppUser.id == user_id).one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="계정을 찾을 수 없습니다.")

    if body.role is not None and body.role not in _ROLES:
        raise HTTPException(status_code=400, detail=f"role 은 {sorted(_ROLES)} 중 하나여야 합니다.")
    if body.status is not None and body.status not in _STATUSES:
        raise HTTPException(
            status_code=400, detail=f"status 는 {sorted(_STATUSES)} 중 하나여야 합니다."
        )
    if body.plan is not None and body.plan not in _PLANS:
        raise HTTPException(status_code=400, detail=f"plan 은 {sorted(_PLANS)} 중 하나여야 합니다.")

    demoting = (body.role is not None and body.role != ROLE_ADMIN) or (
        body.status is not None and body.status != "active"
    )
    if user.role == ROLE_ADMIN and demoting and _active_admin_count(db) <= 1:
        raise HTTPException(
            status_code=400, detail="마지막 관리자 계정은 강등하거나 비활성화할 수 없습니다."
        )

    for field in ("display_name", "email", "role", "status", "plan", "daily_analysis_limit"):
        value = getattr(body, field)
        if value is not None:
            setattr(user, field, value)
    db.commit()
    db.refresh(user)
    return _summary(user)


@router.post("/{user_id}/reset-password", response_model=PasswordResetResponse)
def reset_password(user_id: int, db: Session = DbDep) -> PasswordResetResponse:
    """임시 비밀번호를 발급한다. 값은 이 응답에서만 한 번 보인다."""
    user = db.query(AppUser).filter(AppUser.id == user_id).one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="계정을 찾을 수 없습니다.")
    temporary = secrets.token_urlsafe(9)
    user.password_hash = hash_password(temporary)
    db.commit()
    return PasswordResetResponse(username=user.username, temporary_password=temporary)


def _active_admin_count(db: Session) -> int:
    """활성 관리자 수 — 마지막 관리자를 잠그는 것을 막는 데 쓴다."""
    return (
        db.query(AppUser)
        .filter(AppUser.role == ROLE_ADMIN, AppUser.status == "active")
        .count()
    )
