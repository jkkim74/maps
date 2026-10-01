"""Shared fail-closed readiness checks for every automatic BUY path."""

from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from maps.common.models import CandidateSnapshot, CollectionLog, MarketRegimeLog, SecurityMetadata
from maps.common.settings import MapsSettings
from maps.market.trading_rules import previous_trading_day


def metadata_quality_ready(quality: dict | None) -> bool:
    """Validate the recorded counts, never trust a cached boolean or scope tag."""
    if not isinstance(quality, dict):
        return False
    markets = quality.get("markets") or {}
    if not isinstance(markets, dict):
        return False
    for market in ("KOSPI", "KOSDAQ"):
        item = markets.get(market) or {}
        if not isinstance(item, dict):
            return False
        try:
            expected, valid = int(item["expected_count"]), int(item["valid_count"])
        except (KeyError, TypeError, ValueError, OverflowError):
            return False
        if item.get("error") or expected <= 0 or not 0 <= valid <= expected or valid / expected < .95:
            return False
    return True


def collection_metadata_ready(db: Session, ref_date: dt.date, ticker: str | None = None) -> tuple[bool, str | None]:
    """Only the exact requested collection date can authorize new entries."""
    row = (db.query(CollectionLog)
           .filter(CollectionLog.ref_date == ref_date, CollectionLog.source == "krx")
           .order_by(CollectionLog.id.desc()).first())
    if row is None or row.metadata_quality is None:
        return False, "metadata_quality_legacy_unknown"
    if row.status == "failed" or not metadata_quality_ready(row.metadata_quality):
        return False, "metadata_quality_insufficient"
    if ticker:
        markets = row.metadata_quality["markets"].values()
        if any(ticker in item.get("missing_tickers", []) for item in markets):
            return False, "ticker_metadata_incomplete"
        if any(ticker in item.get("listing_date_missing_tickers", []) for item in markets):
            metadata = db.query(SecurityMetadata).filter(SecurityMetadata.ticker == ticker).first()
            if metadata is None or metadata.listing_date is None or metadata.listing_date > ref_date:
                return False, "ticker_metadata_incomplete"
    return True, None


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
    ready, reason = market_score_ready(db, expected)
    return collection_metadata_ready(db, expected) if ready else (ready, reason)


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
    if candidate.score_scope == "research":
        return False, "research_score_only"
    return collection_metadata_ready(db, candidate.ref_date, candidate.ticker)
