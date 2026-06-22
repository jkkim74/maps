"""주문 실패 관측성 수정 테스트.

연속 실패 시 실제 거부 사유가 WARNING 로그와 kill_switch_log.value에 보존되는지 검증한다.
(기존엔 DEBUG 로깅이라 INFO 운영 로그에 남지 않아 거부 원인 추적이 불가능했음)
"""

from __future__ import annotations

import logging

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import maps.common.models  # noqa: F401 — 모델 등록
from maps.common.db import Base
from maps.common.models import KillSwitchLog
from maps.execution.mock_broker import MockBroker
from maps.risk.manager import RiskManager


def _factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine)


def test_consecutive_failure_persists_reason(caplog) -> None:
    engine, factory = _factory()
    db = factory()
    try:
        rm = RiskManager(MockBroker(), db)
        event = None
        with caplog.at_level(logging.WARNING, logger="maps.risk.manager"):
            for _ in range(5):
                event = rm.on_order_failure("donchian_v2", reason="APBK0919 주문가능수량 부족")

        assert event is not None  # 5회째 Kill Switch 발동

        row = (
            db.query(KillSwitchLog)
            .filter(KillSwitchLog.event_type == "trigger")
            .order_by(KillSwitchLog.id.desc())
            .first()
        )
        assert row is not None
        assert row.reason == "consecutive_failure"        # 카테고리는 유지
        assert "APBK0919" in (row.value or "")             # 실제 거부 사유가 DB(value)에 보존

        # 개별 실패가 사유와 함께 WARNING으로 남는지
        msgs = [r.getMessage() for r in caplog.records]
        assert any("주문 실패" in m and "APBK0919" in m for m in msgs)
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_failure_counter_resets_on_success() -> None:
    engine, factory = _factory()
    db = factory()
    try:
        rm = RiskManager(MockBroker(), db)
        for _ in range(4):
            rm.on_order_failure("s1", reason="x")
        rm.on_order_success("s1")            # 카운터 리셋
        event = rm.on_order_failure("s1", reason="y")
        assert event is None                 # 1회차 → 미발동
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()
