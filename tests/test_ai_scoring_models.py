"""Persistence tests for Phase 2 AI scoring provenance and cache rows."""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy.exc import IntegrityError

from maps.common.models import AIScoringInvocation, CandidateSnapshot


def test_candidate_snapshot_stores_score_provenance(db) -> None:
    """Candidate snapshots retain rule, AI, and recommendation provenance."""
    row = CandidateSnapshot(
        ref_date=dt.date(2026, 8, 7),
        strategy_id="pullback_v3",
        ticker="005930",
        name="삼성전자",
        market="KOSPI",
        factor_score=80,
        trend_strength=70,
        ts_bucket="S4",
        final_score=76,
        rule_score=75,
        recommendation_score=76,
        score_source="AI",
        ai_scoring_mode="rerank",
        ai_status="SUCCESS",
        ai_confidence=0.82,
        ai_reason_codes=["UPTREND"],
        ai_model_id="us.anthropic.claude-sonnet-4-6",
        weekly_pass=True,
    )
    db.add(row)
    db.commit()

    saved = db.query(CandidateSnapshot).one()
    assert saved.rule_score == 75
    assert saved.recommendation_score == saved.final_score == 76
    assert saved.score_source == "AI"


def test_ai_invocation_unique_cache_key(db) -> None:
    """A same-day identical model request can only be reserved once."""
    kwargs = {
        "ref_date": dt.date(2026, 8, 7),
        "ticker": "005930",
        "input_hash": "a" * 64,
        "model_id": "us.anthropic.claude-sonnet-4-6",
        "prompt_version": "ai-score-v1",
        "status": "STARTED",
        "input_tokens": 0,
        "output_tokens": 0,
    }
    db.add(AIScoringInvocation(**kwargs))
    db.commit()
    db.add(AIScoringInvocation(**kwargs))

    with pytest.raises(IntegrityError):
        db.commit()
