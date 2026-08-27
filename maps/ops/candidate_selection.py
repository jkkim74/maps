"""Shared SQL expressions for AI-aware candidate order eligibility."""

from __future__ import annotations

from sqlalchemy import and_, case, func, or_
from sqlalchemy.sql.elements import ColumnElement

from maps.common.models import CandidateSnapshot


def candidate_score_complete(candidate: CandidateSnapshot) -> bool:
    """Return whether a persisted candidate score is fully measured."""
    return bool(
        candidate.score_ready
        and float(candidate.score_coverage_ratio or 0.0) >= 1.0
    )


def candidate_score_complete_expression() -> ColumnElement[bool]:
    """Return the SQL equivalent of :func:`candidate_score_complete`."""
    return and_(
        CandidateSnapshot.score_ready.is_(True),
        func.coalesce(CandidateSnapshot.score_coverage_ratio, 0.0) >= 1.0,
    )


def candidate_min_score_expression() -> ColumnElement[float]:
    """Return the score used for the minimum gate in each AI scoring mode."""
    rule_score = func.coalesce(
        CandidateSnapshot.rule_score,
        CandidateSnapshot.final_score,
    )
    return case(
        (CandidateSnapshot.ai_scoring_mode == "rerank", rule_score),
        else_=CandidateSnapshot.final_score,
    )


def candidate_recommendation_eligible_expression() -> ColumnElement[bool]:
    """Exclude only replace-mode rows omitted by the global AI shortlist."""
    return or_(
        CandidateSnapshot.ai_scoring_mode != "replace",
        CandidateSnapshot.ai_status.is_(None),
        CandidateSnapshot.ai_status != "SKIPPED_LIMIT",
    )
