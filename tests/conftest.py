"""pytest 공통 픽스처."""

from __future__ import annotations

import sys

import pytest

# Windows cp949 콘솔에서 한글 경고·로그가 깨지는 문제 수정
# SetConsoleOutputCP(65001) = UTF-8 코드 페이지 강제
if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:
        pass
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from maps.common.db import Base

# 모든 모델 임포트 (메타데이터 등록)
import maps.common.models  # noqa: F401


@pytest.fixture(scope="function")
def db() -> Session:
    """인메모리 SQLite 세션. 테스트마다 새 DB를 생성·삭제한다."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()
