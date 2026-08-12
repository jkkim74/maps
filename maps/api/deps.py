"""공통 FastAPI 의존성."""

from __future__ import annotations

from collections.abc import Generator

from fastapi import Depends
from sqlalchemy.orm import Session

from maps.common import db as db_module


def get_db() -> Generator[Session, None, None]:
    """DB 세션 의존성.

    `SessionLocal` 을 이름으로 가져오지 않고 모듈 속성으로 늦게 조회한다 —
    테스트가 `maps.common.db.SessionLocal` 한 곳만 바꾸면 인증 게이트와 라우터가
    같은 저장소를 보게 된다.
    """
    db = db_module.SessionLocal()
    try:
        yield db
    finally:
        db.close()


DbDep = Depends(get_db)
