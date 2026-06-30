"""후보 주문 경로 _order_qty 의 계좌-위험 사이징 테스트 (C-2)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import maps.common.models  # noqa: F401
from maps.common.db import Base
from maps.ops.scheduler import OperationalPipeline


@pytest.fixture
def pipeline() -> OperationalPipeline:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return OperationalPipeline(session_factory=factory)


def _cand(strategy_id: str, estimated_qty=None) -> SimpleNamespace:
    return SimpleNamespace(strategy_id=strategy_id, estimated_qty=estimated_qty)


def test_order_qty_risk_based_pullback(pipeline: OperationalPipeline) -> None:
    # pullback_v3 손절 5%: 0.5% 위험 사이징이 10% 노출 상한과 동일 → 1000주
    qty = pipeline._order_qty(
        _cand("pullback_v3"),
        total_value=100_000_000,
        remaining_cash=100_000_000,
        price=10_000,
        remaining_slots=1,
    )
    assert qty == 1000


def test_order_qty_risk_parity_wider_stop_smaller(pipeline: OperationalPipeline) -> None:
    # ath_breakout_v1 손절 10%: 손절폭 2배 → 위험 사이징 수량 절반(500주)
    qty = pipeline._order_qty(
        _cand("ath_breakout_v1"),
        total_value=100_000_000,
        remaining_cash=100_000_000,
        price=10_000,
        remaining_slots=1,
    )
    assert qty == 500


def test_order_qty_unknown_strategy_falls_back_fixed(pipeline: OperationalPipeline) -> None:
    # 손절 정보 없는 전략 → 고정비중 폴백 (10% 노출 = 1000주)
    qty = pipeline._order_qty(
        _cand("no_such_strategy"),
        total_value=100_000_000,
        remaining_cash=100_000_000,
        price=10_000,
        remaining_slots=1,
    )
    assert qty == 1000


def test_order_qty_capped_by_cash_slot_budget(pipeline: OperationalPipeline) -> None:
    # 잔여현금/슬롯 공정배분 상한이 위험 사이징보다 작으면 그 상한이 바인딩
    # cash_budget = 2_000_000 / 4 = 500_000 → 500_000 // 10_000 = 50주
    qty = pipeline._order_qty(
        _cand("pullback_v3"),
        total_value=100_000_000,
        remaining_cash=2_000_000,
        price=10_000,
        remaining_slots=4,
    )
    assert qty == 50


def test_order_qty_respects_estimated_qty(pipeline: OperationalPipeline) -> None:
    qty = pipeline._order_qty(
        _cand("pullback_v3", estimated_qty=37),
        total_value=100_000_000,
        remaining_cash=100_000_000,
        price=10_000,
        remaining_slots=1,
    )
    assert qty == 37
