"""maps/tests 공통 픽스처 (인메모리 SQLite db 세션)."""

from __future__ import annotations

import pytest
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


@pytest.fixture(autouse=True)
def _execution_policy_storage(request):
    """Isolate policy reads without building the full schema for pure unit tests."""
    from maps.execution.runtime_policy import policy_database
    if "db" in request.fixturenames:
        with policy_database(request.getfixturevalue("db")):
            yield
        return
    from maps.common.models import ExecutionPolicy
    policy_engine = create_engine("sqlite:///:memory:")
    ExecutionPolicy.__table__.create(policy_engine)
    try:
        with Session(policy_engine) as policy_db, policy_database(policy_db):
            yield
    finally:
        policy_engine.dispose()
