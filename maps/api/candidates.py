"""SCR-04 종목 후보 풀 API."""

from __future__ import annotations

import datetime

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from maps.api.auth import current_identity, load_user
from maps.api.deps import get_db
from maps.api.schemas import CandidatesResponse
from maps.api.schemas import CandidateItem
from maps.api.schemas import UserPreferences
from maps.common.models import CandidateSnapshot, UniverseQualityLog
from maps.common.user_prefs import resolve
from maps.ops.candidate_selection import (
    candidate_min_score_expression,
    candidate_score_complete_expression,
)

router = APIRouter(prefix="/api/v1/candidates", tags=["SCR-04 Candidates"])


def _candidate_item(row: CandidateSnapshot) -> CandidateItem:
    """Serialize one persisted snapshot without changing score provenance."""
    return CandidateItem(
        ticker=row.ticker,
        name=row.name,
        market=row.market,
        factor_score=row.factor_score,
        trend_strength=row.trend_strength,
        ts_bucket=row.ts_bucket,
        final_score=row.final_score,
        rule_score=(row.rule_score if row.rule_score is not None else row.final_score),
        ai_score=row.ai_technical_score,
        recommendation_score=(
            row.recommendation_score
            if row.recommendation_score is not None
            else row.final_score
        ),
        score_source=row.score_source or "RULE",
        ai_scoring_mode=row.ai_scoring_mode or "off",
        ai_status=row.ai_status,
        ai_confidence=row.ai_confidence,
        ai_reason_codes=row.ai_reason_codes,
        ai_model_id=row.ai_model_id,
        score_type=row.score_type,
        strategy_type=row.strategy_type,
        component_scores=row.component_scores,
        component_sources=row.component_sources,
        missing_components=row.missing_components or [],
        score_coverage_ratio=row.score_coverage_ratio,
        score_status=row.score_status,
        score_ready=row.score_ready,
        market_score_ready=row.market_score_ready,
        score_reason=row.score_reason,
        excluded_reason=row.excluded_reason,
        weekly_pass=row.weekly_pass,
        estimated_qty=row.estimated_qty or 0,
        ai_technical_score=row.ai_technical_score,
        ai_buy_price=row.ai_buy_price,
        ai_stop_price=row.ai_stop_price,
        ai_target_price=row.ai_target_price,
        ai_analysis_memo=row.ai_analysis_memo,
        valuation_margin_score=row.valuation_margin_score,
        valuation_margin_reason=row.valuation_margin_reason,
    )


def _viewer_prefs(request: Request, db: Session) -> UserPreferences | None:
    """요청자의 개인 표시 설정. 필터를 걸지 않아야 하면 None.

    인증이 꺼진 환경은 `ANONYMOUS_ADMIN`(id=None)이라 필터 대상이 아니다.
    계정을 못 찾는 경우도 조회 화면이므로 fail-safe 로 전체를 보여 준다.
    """
    identity = current_identity(request)
    if identity.id is None:
        return None
    user = load_user(db, identity.username)
    return resolve(user) if user is not None else None


@router.get("", response_model=CandidatesResponse)
def get_candidates(
    request: Request,
    strategy_id: str = Query(default="pullback_v3"),
    db: Session = Depends(get_db),
) -> CandidatesResponse:
    """전략별 최신 후보 스냅샷을 반환한다."""
    latest_date = (
        db.query(func.max(CandidateSnapshot.ref_date))
        .filter(CandidateSnapshot.strategy_id == strategy_id)
        .scalar()
    )
    if latest_date is None:
        today = datetime.date.today()
        return CandidatesResponse(
            strategy_id=strategy_id,
            universe_count=0,
            s5_excluded=0,
            missing_count=0,
            final_count=0,
            ready_count=0,
            incomplete_count=0,
            candidates=[],
            incomplete_candidates=[],
            ref_date=today.isoformat(),
        )

    final_count = (
        db.query(func.count(CandidateSnapshot.id))
        .filter(
            CandidateSnapshot.strategy_id == strategy_id,
            CandidateSnapshot.ref_date == latest_date,
        )
        .scalar()
        or 0
    )
    complete = candidate_score_complete_expression()
    ready_count = (
        db.query(func.count(CandidateSnapshot.id))
        .filter(
            CandidateSnapshot.strategy_id == strategy_id,
            CandidateSnapshot.ref_date == latest_date,
            complete,
        )
        .scalar()
        or 0
    )
    base_query = db.query(CandidateSnapshot).filter(
        CandidateSnapshot.strategy_id == strategy_id,
        CandidateSnapshot.ref_date == latest_date,
    )
    ready_query = base_query.filter(complete)
    incomplete_query = base_query.filter(~complete)
    prefs = _viewer_prefs(request, db)
    if prefs is not None:
        if prefs.candidate_min_score is not None:
            # 주문 게이트(`ops/order_preview`·`ops/scheduler`)와 **같은 점수 컬럼**을 쓴다.
            # 원시 final_score 로 거르면 rerank 모드에서 화면과 주문이 어긋난다.
            ready_query = ready_query.filter(
                candidate_min_score_expression() >= prefs.candidate_min_score
            )
        if prefs.candidate_markets:
            market_filter = CandidateSnapshot.market.in_(prefs.candidate_markets)
            ready_query = ready_query.filter(market_filter)
            incomplete_query = incomplete_query.filter(market_filter)
    rows = (
        ready_query.order_by(
            CandidateSnapshot.final_score.desc(), CandidateSnapshot.ticker.asc()
        )
        .limit(200)
        .all()
    )
    incomplete_rows = (
        incomplete_query.order_by(
            CandidateSnapshot.score_coverage_ratio.desc(),
            CandidateSnapshot.ticker.asc(),
        )
        .limit(200)
        .all()
    )
    quality = (
        db.query(UniverseQualityLog)
        .filter(UniverseQualityLog.ref_date == latest_date, UniverseQualityLog.mode == "live")
        .order_by(UniverseQualityLog.created_at.desc())
        .first()
    )
    return CandidatesResponse(
        strategy_id=strategy_id,
        universe_count=quality.total_candidates if quality else final_count,
        s5_excluded=0,
        missing_count=quality.excluded_count if quality else 0,
        final_count=final_count,
        ready_count=ready_count,
        incomplete_count=final_count - ready_count,
        candidates=[_candidate_item(row) for row in rows],
        incomplete_candidates=[_candidate_item(row) for row in incomplete_rows],
        ref_date=latest_date.isoformat(),
    )
