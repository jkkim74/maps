"""점수 준비도로 막힌 매수가 로그·잡 결과에 드러나는지 검증한다.

2026-08-12~14 에 후보 10건이 매일 차단됐는데 `order_cycle` 잡 결과는
`"skipped_buy_orders": 0` 이었다. 후보 선별 단계가 **로그 없이** 버렸기 때문이다.
원인 규명이 이틀 넘게 늦어진 직접적인 이유라, 사유별 집계를 계약으로 고정한다.
"""

from __future__ import annotations

import datetime as dt
import logging

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import maps.common.models  # noqa: F401
from maps.common.db import Base
from maps.common.models import CandidateSnapshot, PromotionHistory
from maps.common.settings import MapsSettings
from maps.market.trading_rules import previous_trading_day
from maps.ops.scheduler import OperationalPipeline


@pytest.fixture
def factory():
    """인메모리 세션 팩토리."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.drop_all(engine)
    engine.dispose()


def _seed_blocked_candidate(db, ref_date: dt.date) -> None:
    """준비도만 미달인 후보 1건 + 주문 가능 단계 승격 이력."""
    db.add(CandidateSnapshot(
        ref_date=ref_date,
        strategy_id="ath_breakout_v1",
        ticker="005930",
        name="삼성전자",
        market="KOSPI",
        factor_score=90.0,
        trend_strength=80.0,
        ts_bucket="S4",
        final_score=90.0,
        weekly_pass=True,
        score_ready=False,
        score_coverage_ratio=0.3,
        market_score_ready=False,
    ))
    db.add(PromotionHistory(
        strategy_id="ath_breakout_v1",
        from_stage="alert_only",
        to_stage="mock_candidate",
        tradeability_score=70.0,
        passed=True,
        evaluated_at=dt.datetime(2026, 8, 1),
    ))
    db.commit()


def test_order_candidates_logs_each_readiness_skip(factory, caplog) -> None:
    """준비도로 후보를 버릴 때 ticker·전략·사유가 로그에 남아야 한다."""
    today = dt.date(2026, 8, 14)
    ref_date = previous_trading_day(today)
    db = factory()
    _seed_blocked_candidate(db, ref_date)

    pipeline = OperationalPipeline(
        settings=MapsSettings(
            maps_score_readiness_required=True,
            maps_broker_mode="mock",
            maps_candidate_min_score=5.0,
        ),
        session_factory=factory,
    )

    with caplog.at_level(logging.WARNING, logger="maps.ops.scheduler"):
        result = pipeline._order_candidates(db, today)

    assert result == []
    messages = " | ".join(record.getMessage() for record in caplog.records)
    assert "005930" in messages
    assert "ath_breakout_v1" in messages
    assert "market_score_missing" in messages
    db.close()


def test_order_candidates_reports_blocked_reasons(factory) -> None:
    """사유별 집계를 호출부로 돌려줘야 잡 결과에 실을 수 있다."""
    today = dt.date(2026, 8, 14)
    ref_date = previous_trading_day(today)
    db = factory()
    _seed_blocked_candidate(db, ref_date)

    pipeline = OperationalPipeline(
        settings=MapsSettings(
            maps_score_readiness_required=True,
            maps_broker_mode="mock",
            maps_candidate_min_score=5.0,
        ),
        session_factory=factory,
    )

    blocked: dict[str, int] = {}
    result = pipeline._order_candidates(db, today, blocked=blocked)

    assert result == []
    assert blocked == {"market_score_missing": 1}
    db.close()


def test_order_candidates_keeps_two_argument_call(factory) -> None:
    """기존 2-인자 호출부가 그대로 동작해야 한다 (blocked 는 키워드 전용 기본값)."""
    today = dt.date(2026, 8, 14)
    db = factory()

    pipeline = OperationalPipeline(
        settings=MapsSettings(maps_broker_mode="mock"),
        session_factory=factory,
    )

    assert pipeline._order_candidates(db, today) == []
    db.close()
