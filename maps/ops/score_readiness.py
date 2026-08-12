"""Shared fail-closed readiness checks for every automatic BUY path."""

from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from maps.common.models import CandidateSnapshot, MarketRegimeLog
from maps.common.settings import MapsSettings
from maps.market.trading_rules import previous_trading_day


def market_score_ready(db: Session, ref_date: dt.date) -> tuple[bool, str | None]:
    """Require the persisted market score for the exact observation date."""
    row = db.query(MarketRegimeLog).filter(MarketRegimeLog.ref_date == ref_date).first()
    if row is None:
        return False, "market_score_missing"
    if not row.score_ready or float(row.score_coverage_ratio or 0.0) < 1.0:
        return False, "market_score_incomplete"
    return True, None


def current_market_score_ready(
    db: Session, settings: MapsSettings, order_date: dt.date
) -> tuple[bool, str | None]:
    """Require the completed session immediately preceding an order date."""
    expected = previous_trading_day(order_date, extra_closed_dates=settings.krx_closed_dates)
    return market_score_ready(db, expected)


def candidate_score_ready(
    db: Session, candidate: CandidateSnapshot
) -> tuple[bool, str | None]:
    """Require exact-date complete market and candidate observations."""
    market_ready, reason = market_score_ready(db, candidate.ref_date)
    if not market_ready:
        return False, reason
    if not candidate.market_score_ready:
        return False, "market_score_incomplete"
    if not candidate.score_ready or float(candidate.score_coverage_ratio or 0.0) < 1.0:
        return False, "candidate_score_incomplete"
    return True, None
