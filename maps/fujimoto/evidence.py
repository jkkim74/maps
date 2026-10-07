"""Validated point-in-time evidence; never infer annual dividends from daily DPS."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone, timedelta
from math import isfinite
from statistics import median
from collections.abc import Iterable

import pandas as pd

from maps.common.exceptions import DataQualityError
from maps.data.dart_financials import KST, available_date


def _number(value: float | None, name: str, *, positive: bool = False) -> None:
    """Reject nonfinite numbers while preserving explicit missing values."""
    if value is not None and (not isfinite(value) or (positive and value <= 0)):
        raise DataQualityError(f"invalid_{name}")


def _utc(value: datetime) -> datetime:
    """Use DART/classification's UTC convention for naive database timestamps."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _temporal(record: AnnualRecord | FinancialRecord) -> None:
    """Validate provenance and next-session first-seen availability."""
    if not record.ticker or not record.receipt or record.basis not in {"CFS", "OFS"} or not record.currency:
        raise DataQualityError("invalid_financial_provenance")
    if record.period_end > record.publication_date:
        raise DataQualityError("publication_before_period_end")
    if record.available_date < available_date(record.publication_date, record.first_observed_at):
        raise DataQualityError("backdated_availability")


@dataclass(frozen=True)
class AnnualRecord:
    """One confirmed fiscal year's evidence or a known, pending correction.

    A pending receipt has null amounts. A later populated observation of that
    receipt must retain its actual observation and availability boundary.
    share_basis must identify comparable corporate-action adjusted DPS units.
    """
    ticker: str
    period_end: date
    receipt: str
    publication_date: date
    first_observed_at: datetime
    available_date: date
    basis: str
    currency: str
    share_basis: str | None
    revenue: float | None
    operating_profit: float | None
    dividend_per_share: float | None

    def __post_init__(self) -> None:
        _temporal(self)
        for name in ("revenue", "operating_profit", "dividend_per_share"):
            _number(getattr(self, name), name)


@dataclass(frozen=True)
class FinancialRecord:
    """Latest released cumulative-period IS pair, with comparable prior-year pair."""
    ticker: str
    period_end: date
    receipt: str
    publication_date: date
    first_observed_at: datetime
    available_date: date
    basis: str
    currency: str
    revenue: float | None
    prior_revenue: float | None
    operating_profit: float | None
    prior_operating_profit: float | None
    comparable: bool = True

    def __post_init__(self) -> None:
        _temporal(self)
        for name in ("revenue", "prior_revenue", "operating_profit", "prior_operating_profit"):
            _number(getattr(self, name), name)


@dataclass(frozen=True)
class SectorSnapshot:
    """Complete published historical membership, never current metadata fallback."""
    ref_date: date
    available_at: datetime
    memberships: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.memberships, tuple) or not self.memberships:
            raise DataQualityError("invalid_sector_membership")
        if len({t for t, _ in self.memberships}) != len(self.memberships):
            raise DataQualityError("duplicate_sector_member")
        if any(not t or not s or s.strip().lower() in {"none", "null", "nan"} for t, s in self.memberships):
            raise DataQualityError("invalid_sector_member")


@dataclass(frozen=True)
class ValuationRecord:
    """Dated, actually observed PER; null/nonpositive values never enter median."""
    ticker: str
    ref_date: date
    available_at: datetime
    per: float | None

    def __post_init__(self) -> None:
        if not self.ticker:
            raise DataQualityError("missing_ticker")
        _number(self.per, "per")


@dataclass(frozen=True)
class QualityResult:
    """Auditable blocking reasons, kept separate from deterioration."""
    passed: bool
    reasons: tuple[str, ...] = ()


def _as_of(records: Iterable[AnnualRecord | FinancialRecord], cutoff: date) -> list:
    """Select latest known receipt per period, then its latest available observation."""
    latest = {}
    ticker = None
    for row in records:
        if row.available_date > cutoff:
            continue
        if ticker is not None and row.ticker != ticker:
            raise DataQualityError("mixed_ticker_evidence")
        ticker = row.ticker
        old = latest.get(row.period_end)
        key = (row.publication_date, row.receipt, _utc(row.first_observed_at))
        if old is None or key > (old.publication_date, old.receipt, _utc(old.first_observed_at)):
            latest[row.period_end] = row
    return [latest[d] for d in sorted(latest)]


def annual_quality(records: Iterable[AnnualRecord], cutoff: date) -> QualityResult:
    """Require three consecutive comparable fiscal years with increasing profits/revenue."""
    rows = _as_of(records, cutoff)[-3:]
    if len(rows) != 3:
        return QualityResult(False, ("missing_annual_history",))
    reasons = []
    # Research freshness policy: permit the prior fiscal year until the next
    # year's 90-day publication window ends; no indefinite stale growth pass.
    end = rows[-1].period_end
    next_end = date(end.year + 1, end.month, min(end.day, 28) if end.month == 2 else end.day)
    if cutoff > next_end + timedelta(days=90):
        reasons.append("expired_annual_history")
    if len({r.ticker for r in rows}) != 1:
        raise DataQualityError("mixed_ticker_evidence")
    if any(b.period_end.year != a.period_end.year + 1 or
           (b.period_end.month, b.period_end.day) != (a.period_end.month, a.period_end.day)
           for a, b in zip(rows, rows[1:])):
        reasons.append("nonconsecutive_annual_history")
    if (len({(r.basis, r.currency, r.share_basis) for r in rows}) != 1
            or any(not r.share_basis for r in rows)):
        reasons.append("incomparable_annual_history")
    if any(r.revenue is None or r.operating_profit is None for r in rows):
        reasons.append("pending_revision")
    elif (any(r.revenue <= 0 or r.operating_profit <= 0 for r in rows)
          or any(b.revenue <= a.revenue or b.operating_profit <= a.operating_profit
                 for a, b in zip(rows, rows[1:]))):
        reasons.append("annual_growth_failed")
    if any(r.dividend_per_share is None for r in rows):
        reasons.append("missing_dividend")
    elif (any(r.dividend_per_share <= 0 for r in rows)
          or any(b.dividend_per_share < a.dividend_per_share for a, b in zip(rows, rows[1:]))):
        reasons.append("dividend_failed")
    return QualityResult(not reasons, tuple(reasons))


def financial_status(records: Iterable[FinancialRecord], cutoff: date) -> str:
    """DART's 180-day coverage limit; missing/pending data is never deterioration."""
    rows = _as_of(records, cutoff)
    if not rows:
        return "missing"
    if len({r.ticker for r in rows}) != 1:
        raise DataQualityError("mixed_ticker_evidence")
    row = rows[-1]
    amounts = (row.revenue, row.prior_revenue, row.operating_profit, row.prior_operating_profit)
    if not row.comparable or (cutoff - row.period_end).days > 180 or any(v is None for v in amounts):
        return "missing"
    if row.revenue < row.prior_revenue or row.operating_profit < row.prior_operating_profit or row.operating_profit < 0:
        return "deteriorated"
    return "maintained"


@dataclass(frozen=True)
class SelectionResult:
    """Historical screening result and reproducible liquidity ordering inputs."""
    ticker: str
    passed: bool
    reasons: tuple[str, ...]
    sector_median_per: float | None = None
    return_20: float | None = None
    average_turnover_20: float | None = None


def screen(ticker: str, annual_records: Iterable[AnnualRecord], valuations: Iterable[ValuationRecord],
           sectors: SectorSnapshot | None, prices: pd.DataFrame, cutoff: date, *,
           eligible: bool = False) -> SelectionResult:
    """Screen only explicit eligible KOSPI/KOSDAQ ordinary-share historical evidence.

    eligible is the existing DataQualityFilter's historical eligibility result.
    Sector/PER inputs must be exact cutoff snapshots, not latest current values.
    """
    rows = list(annual_records)
    if any(r.ticker != ticker for r in rows):
        raise DataQualityError("mixed_ticker_evidence")
    reasons = list(annual_quality(rows, cutoff).reasons)
    if not eligible:
        reasons.append("ineligible_security")
    membership = {}
    if (sectors is None or sectors.ref_date != cutoff
            or _utc(sectors.available_at).astimezone(KST).date() > cutoff):
        reasons.append("missing_historical_sector")
    else:
        membership = dict(sectors.memberships)
        if ticker not in membership:
            reasons.append("missing_historical_sector")
    dated = {}
    for row in valuations:
        if row.ref_date == cutoff and _utc(row.available_at).astimezone(KST).date() <= cutoff:
            old = dated.get(row.ticker)
            if old is None or _utc(row.available_at) > _utc(old.available_at):
                dated[row.ticker] = row
    peer_pers = [r.per for t, r in dated.items() if ticker in membership and
                 membership.get(t) == membership[ticker] and r.per is not None and r.per > 0]
    peers = {t for t, sector in membership.items() if sector == membership.get(ticker)}
    if any(t not in dated or dated[t].per is None for t in peers):
        reasons.append("incomplete_sector_valuation")
    sector_median = median(peer_pers) if peer_pers else None
    own = dated.get(ticker)
    if own is None or own.per is None or own.per <= 0 or sector_median is None:
        reasons.append("missing_valuation")
    elif own.per > sector_median:
        reasons.append("expensive_per")
    ret = turnover = None
    from maps.fujimoto.indicators import daily_bars, has_session_gap
    frame = daily_bars(prices, cutoff)
    if len(frame) < 21 or frame.empty or frame.index[-1].date() != cutoff:
        reasons.append("missing_price_history")
    else:
        if has_session_gap(frame.iloc[-21:], cutoff):
            reasons.append("price_session_gap")
        ret = float(frame.close.iloc[-1] / frame.close.iloc[-21] - 1)
        if ret > 0.20 + 1e-12:
            reasons.append("rapid_rise")
        if "turnover" not in frame or frame.turnover.iloc[-20:].isna().any():
            reasons.append("missing_turnover")
        else:
            turnover = float(frame.turnover.iloc[-20:].mean())
    return SelectionResult(ticker, not reasons, tuple(reasons), sector_median, ret, turnover)


def rank_candidates(results: Iterable[SelectionResult]) -> list[SelectionResult]:
    """Passed candidates by 20-session traded value descending, ticker ascending."""
    return sorted((r for r in results if r.passed and r.average_turnover_20 is not None),
                  key=lambda r: (-r.average_turnover_20, r.ticker))
