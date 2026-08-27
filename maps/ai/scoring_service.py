"""Durable global budget and cache orchestration for candidate AI scoring."""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
from dataclasses import dataclass
from typing import Mapping

import pandas as pd
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from maps.ai.scoring import AIStockScore, recommendation_score
from maps.ai.technical_scorer import (
    PROMPT_VERSION,
    AIStockFeatures,
    AITechnicalScorer,
)
from maps.common.models import AIScoringInvocation, CandidateSnapshot
from maps.common.settings import MapsSettings
from maps.ops.candidate_selection import candidate_score_complete
from maps.strategy.holding_type import HoldingTypeClassifier, HoldingTypeInput


logger = logging.getLogger(__name__)
_BUDGET_STATUSES = ("STARTED", "SUCCESS", "FAILED")


@dataclass
class AIScoringRunSummary:
    """Mutable candidate-job AI call, cache, outcome, and token totals."""

    targets: int = 0
    calls: int = 0
    cache_hits: int = 0
    successes: int = 0
    failures: int = 0
    skipped_limit: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
class AIStockScoringService:
    """Apply one bounded global AI pass to persisted rule-only candidates."""

    def __init__(
        self,
        *,
        settings: MapsSettings,
        scorer: AITechnicalScorer | None = None,
    ) -> None:
        self._settings = settings
        self._scorer = scorer

    def apply(
        self,
        db: Session,
        ref_date: dt.date,
        frames: Mapping[str, pd.DataFrame],
        active_strategy_ids: set[str],
    ) -> AIScoringRunSummary:
        """Score eligible unique tickers without exceeding durable daily budget."""
        if not active_strategy_ids:
            return AIScoringRunSummary()
        rows = (
            db.query(CandidateSnapshot)
            .filter(
                CandidateSnapshot.ref_date == ref_date,
                CandidateSnapshot.strategy_id.in_(active_strategy_ids),
            )
            .all()
        )
        mode = self._settings.maps_ai_scoring_mode
        self._reset_rule_provenance(rows, mode)
        if mode == "off":
            db.commit()
            return AIScoringRunSummary()

        eligible = [row for row in rows if self._is_eligible(row)]
        groups: dict[str, list[CandidateSnapshot]] = {}
        for row in eligible:
            groups.setdefault(row.ticker, []).append(row)
        ranked_groups = sorted(
            groups.items(),
            key=lambda item: (
                -max(self._rule_score(row) for row in item[1]),
                item[0],
            ),
        )
        summary = AIScoringRunSummary(targets=len(ranked_groups))
        scorer = self._scorer or AITechnicalScorer.from_settings()
        if not scorer.is_configured:
            for group_rows in groups.values():
                for row in group_rows:
                    row.ai_status = "SKIPPED_UNCONFIGURED"
            db.commit()
            return summary

        used_budget = (
            db.query(AIScoringInvocation)
            .filter(
                AIScoringInvocation.ref_date == ref_date,
                AIScoringInvocation.status.in_(_BUDGET_STATUSES),
            )
            .count()
        )
        for ticker, ticker_rows in ranked_groups:
            try:
                features = self._build_features(
                    ticker, ticker_rows, ref_date, frames[ticker]
                )
            except Exception as exc:  # local feature failures never abort the job
                summary.failures += 1
                self._mark_failure(ticker_rows, scorer.model_id, "FAILED")
                logger.warning(
                    "AI scoring feature failure ticker=%s error=%s model=%s",
                    ticker,
                    type(exc).__name__,
                    scorer.model_id,
                )
                continue

            input_hash = hashlib.sha256(
                features.canonical_json().encode("utf-8")
            ).hexdigest()
            cached = self._cached_invocation(
                db, ref_date, ticker, input_hash, scorer.model_id
            )
            if cached is not None:
                summary.cache_hits += 1
                self._apply_cached(ticker_rows, cached, features.strategy_ids)
                continue

            if used_budget >= self._settings.maps_ai_daily_call_limit:
                summary.skipped_limit += 1
                for row in ticker_rows:
                    row.ai_status = "SKIPPED_LIMIT"
                continue

            reservation = AIScoringInvocation(
                ref_date=ref_date,
                ticker=ticker,
                input_hash=input_hash,
                model_id=scorer.model_id,
                prompt_version=PROMPT_VERSION,
                status="STARTED",
                input_tokens=0,
                output_tokens=0,
            )
            db.add(reservation)
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                cached = self._cached_invocation(
                    db, ref_date, ticker, input_hash, scorer.model_id
                )
                if cached is not None:
                    summary.cache_hits += 1
                    self._apply_cached(ticker_rows, cached, features.strategy_ids)
                    continue
                raise

            used_budget += 1
            summary.calls += 1
            try:
                response = scorer.score(features)
            except Exception as exc:  # typed and unexpected provider errors both fall back
                reservation.status = "FAILED"
                reservation.error_code = type(exc).__name__
                self._mark_failure(ticker_rows, scorer.model_id, "FAILED")
                summary.failures += 1
                logger.warning(
                    "AI scoring failed ticker=%s error=%s model=%s",
                    ticker,
                    type(exc).__name__,
                    scorer.model_id,
                )
            else:
                reservation.status = "SUCCESS"
                reservation.score_payload = response.score.to_payload()
                reservation.input_tokens = response.input_tokens
                reservation.output_tokens = response.output_tokens
                self._apply_success(ticker_rows, response.score, scorer.model_id)
                summary.successes += 1
                summary.input_tokens += response.input_tokens
                summary.output_tokens += response.output_tokens
            db.commit()

        db.commit()
        return summary

    def _reset_rule_provenance(
        self,
        rows: list[CandidateSnapshot],
        mode: str,
    ) -> None:
        """Restore every active row to its authoritative rule-only state."""
        for row in rows:
            rule = self._rule_score(row)
            row.rule_score = rule
            row.recommendation_score = rule
            row.final_score = rule
            row.score_source = "RULE"
            row.ai_scoring_mode = mode
            row.ai_status = None
            row.ai_technical_score = None
            row.ai_confidence = None
            row.ai_reason_codes = None
            row.ai_model_id = None
            row.ai_analysis_memo = None
            if self._settings.maps_ai_analysis_mode == "technical_only":
                row.ai_contrarian_score = None
                row.ai_contrarian_opinion = None
                row.ai_contrarian_reason = None
                row.ai_contrarian_thesis = None
                row.ai_contrarian_anti_thesis = None

    def _is_eligible(self, row: CandidateSnapshot) -> bool:
        """Return whether rule gates allow this row to be an AI target."""
        return (
            candidate_score_complete(row)
            and row.entry_signal is True
            and row.weekly_pass is True
            and not row.excluded_reason
            and self._rule_score(row) >= self._settings.maps_candidate_min_score
        )

    @staticmethod
    def _rule_score(row: CandidateSnapshot) -> float:
        """Read the persisted rule score with a legacy final-score fallback."""
        return float(row.rule_score if row.rule_score is not None else row.final_score)

    @staticmethod
    def _build_features(
        ticker: str,
        rows: list[CandidateSnapshot],
        ref_date: dt.date,
        frame: pd.DataFrame,
    ) -> AIStockFeatures:
        """Build one feature payload shared by every eligible strategy row."""
        first = sorted(rows, key=lambda row: row.strategy_id)[0]
        return AIStockFeatures.from_frame(
            ticker=ticker,
            name=first.name,
            ref_date=ref_date.isoformat(),
            frame=frame,
            strategy_ids=tuple(row.strategy_id for row in rows),
            trend_strength=max(float(row.trend_strength) for row in rows),
            ts_bucket=first.ts_bucket,
        )

    @staticmethod
    def _cached_invocation(
        db: Session,
        ref_date: dt.date,
        ticker: str,
        input_hash: str,
        model_id: str,
    ) -> AIScoringInvocation | None:
        """Find an exact same-day cache or prior request reservation."""
        return (
            db.query(AIScoringInvocation)
            .filter(
                AIScoringInvocation.ref_date == ref_date,
                AIScoringInvocation.ticker == ticker,
                AIScoringInvocation.input_hash == input_hash,
                AIScoringInvocation.model_id == model_id,
                AIScoringInvocation.prompt_version == PROMPT_VERSION,
            )
            .one_or_none()
        )

    def _apply_cached(
        self,
        rows: list[CandidateSnapshot],
        invocation: AIScoringInvocation,
        strategy_ids: tuple[str, ...],
    ) -> None:
        """Apply a validated successful cache or preserve a prior failure fallback."""
        if invocation.status == "SUCCESS" and invocation.score_payload is not None:
            score = AIStockScore.from_payload(invocation.score_payload, strategy_ids)
            self._apply_success(rows, score, invocation.model_id)
            return
        self._mark_failure(rows, invocation.model_id, invocation.status)

    def _apply_success(
        self,
        rows: list[CandidateSnapshot],
        score: AIStockScore,
        model_id: str,
    ) -> None:
        """Persist strategy-specific totals and mode-specific recommendations."""
        for row in rows:
            rule = self._rule_score(row)
            ai_score = score.score_for(row.strategy_id)
            row.ai_technical_score = ai_score
            row.recommendation_score = recommendation_score(
                self._settings.maps_ai_scoring_mode,
                rule_score=rule,
                ai_score=ai_score,
                weight=self._settings.maps_ai_rerank_weight,
            )
            row.final_score = row.recommendation_score
            row.score_source = "AI"
            row.ai_status = "SUCCESS"
            row.ai_confidence = score.confidence
            row.ai_reason_codes = list(score.reason_codes)
            row.ai_model_id = model_id
            self._apply_contrarian(row, score)

    def _apply_contrarian(
        self,
        row: CandidateSnapshot,
        score: AIStockScore,
    ) -> None:
        """Map the optional combined contrarian result without another model call."""
        if not (
            self._settings.maps_ai_contrarian_check_enabled
            and self._settings.maps_ai_analysis_mode == "all"
        ):
            return
        row.ai_contrarian_opinion = score.contrarian_opinion
        row.ai_contrarian_score = score.contrarian_score
        row.holding_type = HoldingTypeClassifier().classify(
            HoldingTypeInput(
                strategy_type=row.strategy_type,
                valuation_margin_score=row.valuation_margin_score,
                excluded_reason=row.excluded_reason,
                ai_contrarian_opinion=score.contrarian_opinion,
            )
        ).value

    @staticmethod
    def _mark_failure(
        rows: list[CandidateSnapshot],
        model_id: str,
        status: str,
    ) -> None:
        """Preserve rule scores and expose an attempted-call fallback source."""
        for row in rows:
            row.score_source = "RULE_FALLBACK"
            row.ai_status = status
            row.ai_model_id = model_id
