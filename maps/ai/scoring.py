"""Validated score payload and mode formulas for candidate AI scoring."""

from __future__ import annotations

from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    ValidationError,
    field_validator,
    model_validator,
)

from maps.common.exceptions import AIScoringResponseError
from maps.common.settings import AIScoringMode


AIReasonCode = Literal[
    "UPTREND",
    "DOWNTREND",
    "MOMENTUM_POSITIVE",
    "MOMENTUM_WEAK",
    "VOLUME_CONFIRMED",
    "VOLUME_WEAK",
    "LOW_VOLATILITY",
    "HIGH_VOLATILITY",
    "HEALTHY_PULLBACK",
    "BREAKOUT_CONFIRMED",
    "OVEREXTENDED",
    "NEAR_SUPPORT",
    "RESISTANCE_OVERHEAD",
    "CONFLICTING_SIGNALS",
    "INSUFFICIENT_DATA",
]


class AIStrategyFit(BaseModel):
    """One bounded strategy-specific rubric component."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy_id: str = Field(min_length=1)
    score: StrictInt = Field(ge=0, le=10)


class AIStockScore(BaseModel):
    """Strict immutable structured output validated by Pydantic."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    trend: StrictInt = Field(ge=0, le=25)
    momentum: StrictInt = Field(ge=0, le=20)
    volume: StrictInt = Field(ge=0, le=15)
    risk: StrictInt = Field(ge=0, le=15)
    timing: StrictInt = Field(ge=0, le=15)
    strategy_fit: tuple[AIStrategyFit, ...] = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    reason_codes: tuple[AIReasonCode, ...] = Field(max_length=3)
    contrarian_opinion: Literal["NONE", "PASS", "WATCH", "REJECT"]
    contrarian_score: float | None = Field(ge=0.0, le=100.0)

    @field_validator("confidence", "contrarian_score", mode="before")
    @classmethod
    def _reject_boolean_numbers(cls, value: object) -> object:
        """Reject booleans at numeric trust boundaries."""
        if isinstance(value, bool):
            raise ValueError("boolean is not a numeric score")
        return value

    @model_validator(mode="after")
    def _reject_duplicates(self) -> "AIStockScore":
        """Reject duplicate strategy fits and reason codes."""
        strategy_ids = [item.strategy_id for item in self.strategy_fit]
        if len(strategy_ids) != len(set(strategy_ids)):
            raise ValueError("duplicate strategy fit")
        if len(self.reason_codes) != len(set(self.reason_codes)):
            raise ValueError("duplicate reason code")
        return self

    @classmethod
    def from_payload(
        cls,
        payload: object,
        expected_strategy_ids: tuple[str, ...],
    ) -> "AIStockScore":
        """Validate provider JSON and its exact requested strategy set."""
        try:
            score = cls.model_validate(payload)
        except ValidationError as exc:
            raise AIScoringResponseError("Invalid structured AI score") from exc
        actual = [item.strategy_id for item in score.strategy_fit]
        if set(actual) != set(expected_strategy_ids) or len(actual) != len(expected_strategy_ids):
            raise AIScoringResponseError(
                "strategy_fit must exactly match the requested strategy IDs"
            )
        return score

    @property
    def common_score(self) -> int:
        """Return the common five-component subtotal."""
        return self.trend + self.momentum + self.volume + self.risk + self.timing

    def score_for(self, strategy_id: str) -> float:
        """Return the server-computed total for one requested strategy."""
        fit = next(
            (item.score for item in self.strategy_fit if item.strategy_id == strategy_id),
            None,
        )
        if fit is None:
            raise AIScoringResponseError(f"No validated strategy fit for {strategy_id}")
        return float(self.common_score + fit)

    def to_payload(self) -> dict[str, object]:
        """Return JSON-compatible data for the durable same-day cache."""
        return self.model_dump(mode="json")


def recommendation_score(
    mode: AIScoringMode,
    *,
    rule_score: float,
    ai_score: float | None,
    weight: float,
) -> float:
    """Calculate recommendation score without altering rule eligibility."""
    if ai_score is None or mode == "off":
        return round(rule_score, 2)
    if mode == "rerank":
        return round(rule_score * (1.0 - weight) + ai_score * weight, 2)
    return round(ai_score, 2)
