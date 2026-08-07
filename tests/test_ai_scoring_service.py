"""Durable budget, cache, and deduplication tests for AI scoring service."""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from maps.ai.scoring import AIStockScore
from maps.ai.scoring_service import AIStockScoringService
from maps.ai.technical_scorer import BedrockScoreResponse
from maps.common.exceptions import AIScoringProviderError
from maps.common.models import AIScoringInvocation, CandidateSnapshot
from maps.common.settings import MapsSettings


REF_DATE = dt.date(2026, 8, 7)


class FakeScorer:
    """Deterministic scorer recording each compact feature request."""

    def __init__(self, *, configured: bool = True, failing: bool = False) -> None:
        self.is_configured = configured
        self.model_id = "test-model"
        self.failing = failing
        self.calls = []

    def score(self, features):
        """Record one call and return a valid per-strategy result or fail."""
        self.calls.append(features)
        if self.failing:
            raise AIScoringProviderError("TimeoutError")
        score = AIStockScore.from_payload(
            {
                "trend": 20,
                "momentum": 15,
                "volume": 10,
                "risk": 12,
                "timing": 10,
                "strategy_fit": [
                    {"strategy_id": strategy_id, "score": 8}
                    for strategy_id in features.strategy_ids
                ],
                "confidence": 0.8,
                "reason_codes": ["UPTREND"],
                "contrarian_opinion": "NONE",
                "contrarian_score": None,
            },
            features.strategy_ids,
        )
        return BedrockScoreResponse(
            score=score,
            input_tokens=400,
            output_tokens=80,
            raw_payload=score.to_payload(),
        )


def _frame() -> pd.DataFrame:
    """Return enough deterministic OHLCV history to build compact features."""
    rng = np.random.default_rng(7)
    close = 50_000 + np.cumsum(rng.normal(30, 300, 80))
    return pd.DataFrame(
        {
            "open": close * 0.99,
            "high": close * 1.01,
            "low": close * 0.98,
            "close": close,
            "volume": rng.integers(100_000, 900_000, 80),
        },
        index=pd.bdate_range(end=REF_DATE, periods=80),
    )


def _settings(mode: str = "rerank", limit: int = 5) -> MapsSettings:
    """Return explicit Phase 2 settings insulated from legacy environment values."""
    return MapsSettings(
        maps_ai_scoring_mode=mode,
        maps_ai_daily_call_limit=limit,
        maps_ai_rerank_weight=0.20,
        maps_candidate_min_score=10,
    )


def _seed_candidate(
    db,
    *,
    ticker: str,
    strategy_id: str = "pullback_v3",
    rule_score: float = 80,
    entry_signal: bool = True,
    weekly_pass: bool = True,
    excluded_reason: str | None = None,
) -> None:
    """Persist one rule-only candidate row."""
    db.add(
        CandidateSnapshot(
            ref_date=REF_DATE,
            strategy_id=strategy_id,
            ticker=ticker,
            name=ticker,
            market="KOSPI",
            factor_score=80,
            trend_strength=70,
            ts_bucket="S4",
            final_score=rule_score,
            rule_score=rule_score,
            recommendation_score=rule_score,
            score_source="RULE",
            ai_scoring_mode="off",
            entry_signal=entry_signal,
            weekly_pass=weekly_pass,
            excluded_reason=excluded_reason,
        )
    )
    db.commit()


def test_service_scores_one_ticker_once_across_strategies(db) -> None:
    """Duplicate tickers share one call and strategy-specific fit scores."""
    _seed_candidate(db, ticker="005930", strategy_id="pullback_v3", rule_score=80)
    _seed_candidate(db, ticker="005930", strategy_id="donchian_v2", rule_score=75)
    scorer = FakeScorer()

    summary = AIStockScoringService(settings=_settings(), scorer=scorer).apply(
        db,
        REF_DATE,
        {"005930": _frame()},
        {"pullback_v3", "donchian_v2"},
    )

    assert summary.calls == 1
    assert scorer.calls[0].strategy_ids == ("donchian_v2", "pullback_v3")
    assert {row.score_source for row in db.query(CandidateSnapshot)} == {"AI"}


def test_service_only_targets_rule_eligible_signals(db) -> None:
    """Signals, weekly gate, exclusions, and rule minimum bound target selection."""
    _seed_candidate(db, ticker="SIGNAL", rule_score=80)
    _seed_candidate(db, ticker="NO_SIGNAL", rule_score=99, entry_signal=False)
    _seed_candidate(db, ticker="BLOCKED", rule_score=98, weekly_pass=False)
    _seed_candidate(db, ticker="EXCLUDED", rule_score=97, excluded_reason="sector")
    scorer = FakeScorer()
    frames = {ticker: _frame() for ticker in ("SIGNAL", "NO_SIGNAL", "BLOCKED", "EXCLUDED")}

    AIStockScoringService(settings=_settings(), scorer=scorer).apply(
        db, REF_DATE, frames, {"pullback_v3"}
    )

    assert [call.ticker for call in scorer.calls] == ["SIGNAL"]


def test_unconfigured_scorer_consumes_no_budget_or_network_call(db) -> None:
    """Missing credentials create neither reservations nor provider calls."""
    _seed_candidate(db, ticker="005930")
    scorer = FakeScorer(configured=False)

    summary = AIStockScoringService(settings=_settings(), scorer=scorer).apply(
        db, REF_DATE, {"005930": _frame()}, {"pullback_v3"}
    )
    row = db.query(CandidateSnapshot).one()

    assert summary.calls == 0
    assert db.query(AIScoringInvocation).count() == 0
    assert row.score_source == "RULE"
    assert row.ai_status == "SKIPPED_UNCONFIGURED"


def test_daily_limit_counts_failed_calls_and_marks_remaining_rows(db) -> None:
    """Every started request consumes budget even when all calls fail."""
    tickers = [f"T{index}" for index in range(7)]
    for index, ticker in enumerate(tickers):
        _seed_candidate(db, ticker=ticker, rule_score=100 - index)
    scorer = FakeScorer(failing=True)

    summary = AIStockScoringService(
        settings=_settings("replace", 5), scorer=scorer
    ).apply(db, REF_DATE, {ticker: _frame() for ticker in tickers}, {"pullback_v3"})

    assert summary.calls == 5
    assert summary.failures == 5
    assert summary.skipped_limit == 2
    assert (
        db.query(AIScoringInvocation)
        .filter(AIScoringInvocation.status == "FAILED")
        .count()
        == 5
    )


def test_success_cache_is_reused_without_new_call(db) -> None:
    """A same-day identical successful request is reused after reruns."""
    _seed_candidate(db, ticker="005930")
    scorer = FakeScorer()
    service = AIStockScoringService(settings=_settings(), scorer=scorer)
    frames = {"005930": _frame()}

    first = service.apply(db, REF_DATE, frames, {"pullback_v3"})
    second = service.apply(db, REF_DATE, frames, {"pullback_v3"})

    assert first.calls == 1
    assert second.calls == 0
    assert second.cache_hits == 1
    assert len(scorer.calls) == 1


def test_failed_cache_is_not_retried_same_day(db) -> None:
    """A failed reservation prevents repeated same-day provider cost."""
    _seed_candidate(db, ticker="005930")
    scorer = FakeScorer(failing=True)
    service = AIStockScoringService(settings=_settings(), scorer=scorer)
    frames = {"005930": _frame()}

    service.apply(db, REF_DATE, frames, {"pullback_v3"})
    service.apply(db, REF_DATE, frames, {"pullback_v3"})

    assert len(scorer.calls) == 1


@pytest.mark.parametrize("mode", ["rerank", "replace"])
def test_limit_rows_keep_rule_provenance_and_mode(db, mode: str) -> None:
    """Rows beyond the global limit remain observable with explicit status."""
    tickers = [f"T{index}" for index in range(7)]
    for index, ticker in enumerate(tickers):
        _seed_candidate(db, ticker=ticker, rule_score=100 - index)

    AIStockScoringService(settings=_settings(mode, 5), scorer=FakeScorer()).apply(
        db, REF_DATE, {ticker: _frame() for ticker in tickers}, {"pullback_v3"}
    )
    skipped = (
        db.query(CandidateSnapshot)
        .filter(CandidateSnapshot.ai_status == "SKIPPED_LIMIT")
        .all()
    )

    assert len(skipped) == 2
    assert all(row.ai_scoring_mode == mode for row in skipped)
    assert all(row.score_source == "RULE" for row in skipped)
