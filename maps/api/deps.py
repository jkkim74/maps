"""공통 FastAPI 의존성."""

from __future__ import annotations

from collections.abc import Generator

from fastapi import Depends
from sqlalchemy.orm import Session

from maps.common.db import SessionLocal


def get_db() -> Generator[Session, None, None]:
    """DB 세션 의존성."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


DbDep = Depends(get_db)
