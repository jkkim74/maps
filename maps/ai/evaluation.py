"""Provider-free statistics for repeated AI scoring observations."""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from itertools import groupby


@dataclass(frozen=True)
class EvaluationSample:
    """One compact ticker sample used by every compared model."""

    ticker: str
    name: str
    strategy_ids: tuple[str, ...]
    features: dict[str, object]


@dataclass(frozen=True)
class ModelObservation:
    """One model attempt and its safe usage metadata."""

    model_id: str
    ticker: str
    score: float | None
    schema_success: bool
    input_tokens: int
    output_tokens: int
    latency_seconds: float
    error_code: str | None = None


@dataclass(frozen=True)
class ModelEvaluationSummary:
    """Aggregated consistency, usage, and latency for one model."""

    model_id: str
    observation_count: int
    schema_success_rate: float
    score_stddev: float
    input_tokens: int
    output_tokens: int
    mean_latency_seconds: float


def summarize_observations(
    observations: list[ModelObservation],
) -> list[ModelEvaluationSummary]:
    """Summarize repeated observations by model using population deviation."""
    summaries: list[ModelEvaluationSummary] = []
    for model_id, grouped in groupby(
        sorted(observations, key=lambda item: item.model_id),
        key=lambda item: item.model_id,
    ):
        rows = list(grouped)
        scores_by_ticker: dict[str, list[float]] = {}
        for row in rows:
            if row.schema_success and row.score is not None:
                scores_by_ticker.setdefault(row.ticker, []).append(row.score)
        deviations = [statistics.pstdev(scores) for scores in scores_by_ticker.values()]
        summaries.append(
            ModelEvaluationSummary(
                model_id=model_id,
                observation_count=len(rows),
                schema_success_rate=sum(row.schema_success for row in rows) / len(rows),
                score_stddev=statistics.mean(deviations) if deviations else 0.0,
                input_tokens=sum(row.input_tokens for row in rows),
                output_tokens=sum(row.output_tokens for row in rows),
                mean_latency_seconds=statistics.mean(
                    row.latency_seconds for row in rows
                ),
            )
        )
    return summaries
