"""PromotionGate 핵심 원칙 테스트 (Phase 2 + Phase 3).

- 알 수 없는 메트릭 → fail with reason (KeyError 없음)
- MOCK_CANDIDATE 이상 + 알 수 없는 strategy_id → UnknownStrategyError
- mc_within_limit 초과 → fail
- is_cagr=0 → 즉시 fail
"""

from unittest.mock import MagicMock

import pytest

from maps.common.constants import ALLOWED_MDD
from maps.common.exceptions import UnknownStrategyError
from maps.promotion.gate import PromotionGate, PromotionStage


@pytest.fixture
def gate() -> PromotionGate:
    return PromotionGate(db=MagicMock())


# ---------------------------------------------------------------------------
# Phase 2 기존 테스트
# ---------------------------------------------------------------------------

def test_missing_metrics_returns_fail_not_exception(gate: PromotionGate) -> None:
    decision = gate.evaluate("strat_001", {}, PromotionStage.RESEARCH)
    assert not decision.passed
    assert len(decision.reasons) > 0


def test_partial_metrics_does_not_raise(gate: PromotionGate) -> None:
    decision = gate.evaluate("strat_001", {"robustness": 0.8}, PromotionStage.RESEARCH)
    assert not decision.passed


def test_full_metrics_high_score_passes(gate: PromotionGate) -> None:
    decision = gate.evaluate(
        "strat_good",
        {"robustness": 1.0, "risk": 1.0, "recovery": 1.0, "return": 1.0},
        PromotionStage.RESEARCH,
    )
    assert decision.passed
    assert decision.score >= 60


def test_score_below_threshold_fails(gate: PromotionGate) -> None:
    decision = gate.evaluate(
        "strat_weak",
        {"robustness": 0.2, "risk": 0.2, "recovery": 0.2, "return": 0.2},
        PromotionStage.RESEARCH,
    )
    assert not decision.passed


# ---------------------------------------------------------------------------
# Phase 3 신규 테스트
# ---------------------------------------------------------------------------

def test_mc_within_limit(gate: PromotionGate) -> None:
    """MC MDD p95 허용 한도 초과 시 MOCK_CANDIDATE → fail."""
    limit = ALLOWED_MDD["pullback_short"]["mc_p95_limit"]  # 0.18

    # pullback_v3 는 STRATEGY_GROUP_MAP 에 등록되어 있어야 함
    decision = gate.evaluate(
        "pullback_v3",
        {
            "robustness": 1.0,
            "risk": 1.0,
            "recovery": 1.0,
            "return": 1.0,
            "mc_mdd_p95": limit + 0.05,  # 한도 초과
        },
        PromotionStage.MOCK_CANDIDATE,
    )
    assert not decision.passed
    assert any("MC" in r for r in decision.reasons)


def test_mc_within_limit_pass(gate: PromotionGate) -> None:
    """MC MDD p95 허용 한도 이내이면 해당 체크는 통과."""
    limit = ALLOWED_MDD["pullback_short"]["mc_p95_limit"]

    decision = gate.evaluate(
        "pullback_v3",
        {
            "robustness": 1.0,
            "risk": 1.0,
            "recovery": 1.0,
            "return": 1.0,
            "mc_mdd_p95": limit - 0.05,  # 한도 이내
            "mock_months": 3.0,
        },
        PromotionStage.MOCK_CANDIDATE,
    )
    # MC 체크는 통과, 점수 >= 75 여야 MOCK_CANDIDATE 승격
    # 점수=100 >= 75 이므로 통과
    assert decision.passed


def test_mock_to_live_small_requires_three_mock_months_or_replay(gate: PromotionGate) -> None:
    limit = ALLOWED_MDD["pullback_short"]["mc_p95_limit"]

    decision = gate.evaluate(
        "pullback_v3",
        {
            "robustness": 1.0,
            "risk": 1.0,
            "recovery": 1.0,
            "return": 1.0,
            "mc_mdd_p95": limit - 0.05,
            "mock_months": 2.0,
        },
        PromotionStage.MOCK_CANDIDATE,
    )

    assert not decision.passed
    assert any("Live Small" in r for r in decision.reasons)


def test_mock_to_live_small_accepts_equivalent_replay(gate: PromotionGate) -> None:
    limit = ALLOWED_MDD["pullback_short"]["mc_p95_limit"]

    decision = gate.evaluate(
        "pullback_v3",
        {
            "robustness": 1.0,
            "risk": 1.0,
            "recovery": 1.0,
            "return": 1.0,
            "mc_mdd_p95": limit - 0.05,
            "mock_months": 0.0,
            "replay_equivalent_passed": 1.0,
            "replay_trading_days": 63,
        },
        PromotionStage.MOCK_CANDIDATE,
    )

    assert decision.passed


def test_zero_division_cagr(gate: PromotionGate) -> None:
    """is_cagr=0 이면 즉시 fail reason 이 추가된다."""
    decision = gate.evaluate(
        "strat_001",
        {
            "is_cagr": 0.0,
            "robustness": 1.0,
            "risk": 1.0,
            "recovery": 1.0,
            "return": 1.0,
        },
        PromotionStage.RESEARCH,
    )
    assert not decision.passed
    assert any("cagr" in r.lower() for r in decision.reasons)


def test_unknown_strategy_id(gate: PromotionGate) -> None:
    """MOCK_CANDIDATE 이상 단계에서 알 수 없는 strategy_id → UnknownStrategyError."""
    with pytest.raises(UnknownStrategyError):
        gate.evaluate(
            "totally_nonexistent_strategy_xyz",
            {"robustness": 1.0, "risk": 1.0, "recovery": 1.0, "return": 1.0},
            PromotionStage.MOCK_CANDIDATE,
        )


def test_unknown_strategy_research_stage_no_error(gate: PromotionGate) -> None:
    """RESEARCH 단계에서는 알 수 없는 strategy_id 도 예외 없음."""
    decision = gate.evaluate(
        "totally_nonexistent_strategy_xyz",
        {"robustness": 1.0, "risk": 1.0, "recovery": 1.0, "return": 1.0},
        PromotionStage.RESEARCH,
    )
    # 예외 없이 PromotionDecision 반환
    assert isinstance(decision.passed, bool)


def test_all_checks_executed(gate: PromotionGate) -> None:
    """단락(short-circuit) 없이 모든 가드가 실행된다."""
    decision = gate.evaluate(
        "strat_001",
        {
            "is_cagr": 0.0,       # 가드 1 실패
            "mock_sharpe": -0.5,  # 가드 2 실패
            "robustness": 1.0,
            "risk": 1.0,
            "recovery": 1.0,
            "return": 1.0,
        },
        PromotionStage.RESEARCH,
    )
    assert not decision.passed
    # 두 가드 모두 reason 에 포함
    assert len([r for r in decision.reasons if "cagr" in r.lower() or "sharpe" in r.lower()]) >= 2


# ---------------------------------------------------------------------------
# 자동 강등 (mock_candidate → research, 점수 연속 미달)
# ---------------------------------------------------------------------------

import datetime as _dt
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import maps.promotion.gate as gate_module
from maps.common.db import Base
from maps.common.models import PromotionHistory

_LOW_METRICS = {
    # 점수 = (0.3·0.4 + 0.3·0.4 + 0.2·0.4 + 0.2·0.4)·100 = 40 < 강등 임계 50
    "robustness": 0.4, "risk": 0.4, "recovery": 0.4, "return": 0.4,
    "mc_mdd_p95": 0.10, "mock_months": 3.0, "mock_sharpe": 0.2,
}


def _db_gate():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = factory()
    return engine, db, PromotionGate(db=db)


def _seed_failed(db, days_ago: int, score: float = 40.0, *,
                 from_stage: str = "mock_candidate", passed: bool = False) -> None:
    db.add(PromotionHistory(
        strategy_id="pullback_v3",
        from_stage=from_stage,
        to_stage="mock_candidate" if passed else from_stage,
        tradeability_score=score,
        passed=passed,
        evaluated_at=_dt.datetime.now() - _dt.timedelta(days=days_ago),
    ))
    db.commit()


@pytest.fixture
def demote_after_3(monkeypatch):
    monkeypatch.setattr(
        gate_module, "get_settings",
        lambda: SimpleNamespace(maps_demotion_consecutive_evals=3),
    )


def test_demotes_after_consecutive_low_scores(demote_after_3) -> None:
    """점수 <50이 연속 N회면 mock→research 강등 행(passed=True)이 기록된다."""
    engine, db, gate = _db_gate()
    try:
        _seed_failed(db, 2)
        _seed_failed(db, 1)

        decision = gate.evaluate("pullback_v3", _LOW_METRICS, PromotionStage.MOCK_CANDIDATE)

        assert decision.demoted is True
        assert any("자동 강등" in r for r in decision.reasons)
        demotion = (
            db.query(PromotionHistory)
            .filter(PromotionHistory.passed.is_(True))
            .one()
        )
        assert demotion.from_stage == "mock_candidate"
        assert demotion.to_stage == "research"
        # 단계 판정 쿼리(최근 passed=True 행)가 강등 행을 집어야 한다
        latest = (
            db.query(PromotionHistory)
            .filter(PromotionHistory.passed.is_(True))
            .order_by(PromotionHistory.evaluated_at.desc(), PromotionHistory.id.desc())
            .first()
        )
        assert latest.to_stage == "research"
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_no_demotion_below_consecutive_count(demote_after_3) -> None:
    """미달이 N-1회뿐이면 강등하지 않는다."""
    engine, db, gate = _db_gate()
    try:
        _seed_failed(db, 1)  # 오늘 실패 포함 2회 < 3회

        decision = gate.evaluate("pullback_v3", _LOW_METRICS, PromotionStage.MOCK_CANDIDATE)

        assert decision.demoted is False
        assert db.query(PromotionHistory).filter(PromotionHistory.passed.is_(True)).count() == 0
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_no_demotion_when_window_has_score_above_threshold(demote_after_3) -> None:
    """윈도 내 한 번이라도 임계(50) 이상 점수가 있으면 연속 미달이 아니다."""
    engine, db, gate = _db_gate()
    try:
        _seed_failed(db, 2, score=55.0)  # 임계 이상
        _seed_failed(db, 1)

        decision = gate.evaluate("pullback_v3", _LOW_METRICS, PromotionStage.MOCK_CANDIDATE)

        assert decision.demoted is False
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_no_demotion_when_window_contains_promotion_row(demote_after_3) -> None:
    """윈도 안에 승격 행(passed=True)이 있으면 강등하지 않는다."""
    engine, db, gate = _db_gate()
    try:
        _seed_failed(db, 2, score=70.0, from_stage="research", passed=True)  # 승격 행
        _seed_failed(db, 1)

        decision = gate.evaluate("pullback_v3", _LOW_METRICS, PromotionStage.MOCK_CANDIDATE)

        assert decision.demoted is False
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_no_demotion_at_research_stage(demote_after_3) -> None:
    """강등은 mock_candidate 전용 — research 저점수 반복은 대상 아님."""
    engine, db, gate = _db_gate()
    try:
        _seed_failed(db, 2, from_stage="research")
        _seed_failed(db, 1, from_stage="research")

        decision = gate.evaluate("pullback_v3", _LOW_METRICS, PromotionStage.RESEARCH)

        assert decision.demoted is False
        assert db.query(PromotionHistory).filter(PromotionHistory.passed.is_(True)).count() == 0
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()
