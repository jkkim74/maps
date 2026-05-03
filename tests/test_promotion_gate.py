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
        },
        PromotionStage.MOCK_CANDIDATE,
    )
    # MC 체크는 통과, 점수 >= 75 여야 MOCK_CANDIDATE 승격
    # 점수=100 >= 75 이므로 통과
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
