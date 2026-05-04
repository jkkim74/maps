"""데이터베이스 엔진 · 세션 팩토리.

MAPS_DB_URL 환경변수가 없으면 sqlite:///./maps.db 를 기본값으로 사용한다.
"""

from __future__ import annotations

from collections.abc import Generator

from dotenv import load_dotenv
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from maps.common.settings import get_settings

load_dotenv()

_DEFAULT_URL = "sqlite:///./maps.db"


def _make_engine(url: str) -> Engine:
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=connect_args)


def get_engine(url: str | None = None) -> Engine:
    """엔진을 생성한다. url이 None이면 환경변수 → 기본값 순으로 사용."""
    resolved = url or get_settings().maps_db_url or _DEFAULT_URL
    return _make_engine(resolved)


# 모듈 레벨 기본 엔진 (애플리케이션 런타임용)
engine: Engine = get_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    """SQLAlchemy 선언적 베이스."""


def get_db() -> Generator[Session, None, None]:
    """FastAPI 의존성 주입용 DB 세션 제공자."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def make_session(url: str) -> Session:
    """테스트·스크립트용 독립 세션 생성."""
    eng = _make_engine(url)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=eng)
    return factory()
