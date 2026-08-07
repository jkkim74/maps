"""Shared SQL expressions for AI-aware candidate order eligibility."""

from __future__ import annotations

from sqlalchemy import case, func, or_
from sqlalchemy.sql.elements import ColumnElement

from maps.common.models import CandidateSnapshot


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
