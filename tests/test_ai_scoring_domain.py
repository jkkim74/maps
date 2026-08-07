"""Pure validation and formula tests for Phase 2 AI scoring."""

from __future__ import annotations

import copy

import pytest

from maps.ai.scoring import AIStockScore, recommendation_score
from maps.common.exceptions import AIScoringResponseError


VALID_PAYLOAD = {
    "trend": 21,
    "momentum": 15,
    "volume": 11,
    "risk": 12,
    "timing": 10,
    "strategy_fit": [{"strategy_id": "pullback_v3", "score": 8}],
    "confidence": 0.82,
    "reason_codes": ["UPTREND", "HEALTHY_PULLBACK", "VOLUME_WEAK"],
    "contrarian_opinion": "NONE",
    "contrarian_score": None,
}


def test_ai_score_is_server_sum_not_model_total() -> None:
    """The application sums validated rubric components itself."""
    score = AIStockScore.from_payload(VALID_PAYLOAD, ("pullback_v3",))

    assert score.score_for("pullback_v3") == 77.0


@pytest.mark.parametrize(
    ("mode", "expected"),
    [("off", 70.0), ("rerank", 72.0), ("replace", 80.0)],
)
def test_recommendation_score_by_mode(mode: str, expected: float) -> None:
    """Each mode applies the approved score formula."""
    assert (
        recommendation_score(
            mode, rule_score=70, ai_score=80, weight=0.20
        )
        == expected
    )


def test_missing_ai_score_falls_back_to_rule() -> None:
    """Missing model output never removes the rule score."""
    assert (
        recommendation_score(
            "replace", rule_score=70, ai_score=None, weight=0.20
        )
        == 70
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [("trend", 26), ("momentum", -1), ("volume", 16), ("risk", 16), ("timing", 16)],
)
def test_score_ranges_are_rejected(field: str, value: int) -> None:
    """Every rubric component is bounded before it contributes to a score."""
    payload = copy.deepcopy(VALID_PAYLOAD)
    payload[field] = value

    with pytest.raises(AIScoringResponseError):
        AIStockScore.from_payload(payload, ("pullback_v3",))


def test_strategy_fit_must_match_requested_strategies_exactly() -> None:
    """Missing or unknown strategy fits invalidate the entire response."""
    with pytest.raises(AIScoringResponseError):
        AIStockScore.from_payload(
            VALID_PAYLOAD, ("pullback_v3", "donchian_v2")
        )


def test_reason_codes_are_bounded_and_known() -> None:
    """Unknown reason codes are rejected instead of leaking arbitrary text."""
    payload = {**VALID_PAYLOAD, "reason_codes": ["UNKNOWN_CODE"]}

    with pytest.raises(AIScoringResponseError):
        AIStockScore.from_payload(payload, ("pullback_v3",))


def test_boolean_component_is_not_accepted_as_an_integer() -> None:
    """Python booleans must not pass integer score validation."""
    payload = {**VALID_PAYLOAD, "trend": True}

    with pytest.raises(AIScoringResponseError):
        AIStockScore.from_payload(payload, ("pullback_v3",))
