"""수급 수집이 0건으로 끝난 사실이 잡 결과와 감사 로그에 드러나는지 검증한다.

`collect_daily` 는 수급 예외를 삼키고 빈 리스트로 넘어간다 — OHLCV 를 살리려는
의도된 설계다. 하지만 그 뒤로도 `data_collection` 잡이 그냥 `success` 로 끝나서,
수급 0건이면 다음 거래일 신규 매수가 전량 막히는데도 아무 신호가 없었다.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import maps.common.models  # noqa: F401
from maps.common.db import Base
from maps.common.exceptions import DataCollectionError
from maps.common.models import CollectionLog, HistoricalOHLCV
from maps.common.settings import MapsSettings
from maps.data.collector import DataCollector
from maps.data.krx_adapter import MockKRXAdapter
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


class _FlowFailingAdapter(MockKRXAdapter):
    """수급만 실패하는 어댑터 — OHLCV 는 정상."""

    def get_investor_flows(self, ref_date: dt.date):
        raise DataCollectionError("KRX investor flow empty [KOSPI 기관합계 20260813]")


def test_collect_daily_reports_zero_investor_flows(factory) -> None:
    """수급이 실패해도 OHLCV 는 살고, 0건 사실은 결과·감사로그에 남아야 한다."""
    ref_date = dt.date(2026, 8, 13)
    db = factory()

    result = DataCollector(_FlowFailingAdapter(seed_tickers=["005930"]), db).collect_daily(ref_date)

    assert result.investor_flow_count == 0
    assert "기관합계" in (result.investor_flow_error or "")
    assert db.query(HistoricalOHLCV).count() > 0      # OHLCV 는 반드시 살아남는다
    log = db.query(CollectionLog).filter(CollectionLog.source == "krx").one()
    assert log.status == "partial"
    assert "investor_flow" in (log.note or "")
    db.close()


def test_collect_daily_reports_zero_investor_flows_without_exception(factory) -> None:
    """예외 없이 빈 목록을 받는 경로도 같아야 한다 — 예외만 잡는 수정으로는 놓친다."""
    ref_date = dt.date(2026, 8, 13)
    db = factory()

    result = DataCollector(MockKRXAdapter(seed_tickers=["005930"]), db).collect_daily(ref_date)

    assert result.investor_flow_count == 0
    assert result.investor_flow_error is None
    log = db.query(CollectionLog).filter(CollectionLog.source == "krx").one()
    assert log.status == "partial"
    db.close()


def test_collect_data_job_details_expose_investor_flow_count(factory) -> None:
    """잡 결과 details 로 드러나야 journald·job_run_log 양쪽에서 보인다."""
    pipeline = OperationalPipeline(
        settings=MapsSettings(maps_data_provider="mock"),
        session_factory=factory,
    )

    run = pipeline.collect_data(dt.date(2026, 8, 13))

    assert run.status == "success"
    assert run.details["investor_flow_count"] == 0
