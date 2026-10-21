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
    status: str | None = None
    provenance: dict | None = None
    strategy_exclusion: dict | None = None
    provider: str | None = None

    def __post_init__(self) -> None:
        if not self.ticker:
            raise DataQualityError("missing_ticker")
        _number(self.per, "per")
        if self.status not in {None, "numeric", "unknown", "verified_not_applicable"}:
            raise DataQualityError("invalid_valuation_status")
        if self.status == "numeric" and self.per is None:
            raise DataQualityError("invalid_valuation_status")
        if self.status == "verified_not_applicable" and (self.per is not None and self.per > 0):
            raise DataQualityError("invalid_valuation_status")
        if self.provenance is not None or self.status == "verified_not_applicable":
            validate_valuation_provenance(self)



def validate_valuation_provenance(row: ValuationRecord) -> None:
    """Reject malformed, mismatched or future source/review evidence on every rebuild."""
    import re
    from urllib.parse import urlparse
    from maps.fujimoto.repository import fingerprint
    try:
        proof = row.provenance
        source = proof["source"]
        observed = datetime.fromisoformat(source["observed_at"])
        if (source["ticker"] != row.ticker or source["ref_date"] != row.ref_date.isoformat()
                or source["per"] != row.per or _utc(observed) > _utc(row.available_at)
                or _utc(observed) > datetime.now(timezone.utc)
                or source["raw_hash"] != fingerprint(source["raw"])):
            raise ValueError("source_binding")
        def document(url, digest):
            parsed = urlparse(url)
            return (parsed.scheme == "https" and bool(parsed.hostname) and parsed.username is None
                    and parsed.password is None and parsed.port in (None, 443)
                    and re.fullmatch(r"[0-9a-f]{64}", digest))
        def provider(url):
            host = urlparse(url).hostname or ""
            return next((domain for domain in ("krx.co.kr", "fss.or.kr")
                         if host == domain or host.endswith("." + domain)), None)
        if not document(source["source_url"], source["document_hash"]):
            raise ValueError("source_document")
        if row.status == "verified_not_applicable":
            review = proof["review"]
            reviewed = datetime.fromisoformat(review["observed_at"])
            if (review["source_id"] != proof["source_id"] or review["source_hash"] != fingerprint(source)
                    or review["ticker"] != row.ticker or review["ref_date"] != row.ref_date.isoformat()
                    or not _utc(observed) <= _utc(reviewed) <= _utc(row.available_at)
                    or _utc(reviewed) > datetime.now(timezone.utc)
                    or not document(review["contract_url"], review["contract_hash"])
                    or provider(review["contract_url"]) is None
                    or provider(review["contract_url"]) != provider(source["source_url"])
                    or len(review["explanation"].strip()) < 20):
                raise ValueError("review_binding")
    except (TypeError, KeyError, ValueError, AttributeError) as exc:
        raise DataQualityError("invalid_valuation_provenance") from exc


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


def annual_quality(records: Iterable[AnnualRecord], cutoff: date, *, minimum_dividend_growth: float = 0) -> QualityResult:
    """Require three consecutive comparable fiscal years with increasing profits/revenue."""
    rows = _as_of(records, cutoff)[-3:]
    _number(minimum_dividend_growth, "minimum_dividend_growth")
    if isinstance(minimum_dividend_growth, bool) or minimum_dividend_growth is None or not -1 < minimum_dividend_growth <= 1:
        raise DataQualityError("invalid_minimum_dividend_growth")
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
          or any(a.dividend_per_share <= 0 or b.dividend_per_share / a.dividend_per_share - 1 < minimum_dividend_growth - 1e-12 for a, b in zip(rows, rows[1:]))):
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
           eligible: bool = False, maximum_return_20: float = .2,
           minimum_dividend_growth: float = 0, screening_version: int = 1,
           security_classifications: dict | None = None) -> SelectionResult:
    """Screen only explicit eligible KOSPI/KOSDAQ ordinary-share historical evidence.

    eligible is the existing DataQualityFilter's historical eligibility result.
    Sector/PER inputs must be exact cutoff snapshots, not latest current values.
    """
    if type(screening_version) is not int or screening_version not in {1, 2, 3}:
        raise DataQualityError("unsupported_screening_version")
    rows = list(annual_records)
    if any(r.ticker != ticker for r in rows):
        raise DataQualityError("mixed_ticker_evidence")
    _number(maximum_return_20, "maximum_return_20")
    if isinstance(maximum_return_20, bool) or maximum_return_20 is None or not 0 <= maximum_return_20 <= 1:
        raise DataQualityError("invalid_maximum_return_20")
    reasons = list(annual_quality(rows, cutoff, minimum_dividend_growth=minimum_dividend_growth).reasons)
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
    peers = {t for t, sector in membership.items() if sector == membership.get(ticker)}
    excluded = set()
    if screening_version == 3:
        from maps.fujimoto.valuation import security_kind, validate_exclusion
        classifications = security_classifications or {}
        kinds = {t: security_kind(classifications.get(t), t, cutoff) for t in peers | {ticker}}
        if any(kinds[t] == "unknown" for t in peers | {ticker}):
            reasons.append("missing_security_classification")
        if kinds[ticker] == "excluded":
            reasons.append("excluded_security")
        peers = {t for t in peers if kinds[t] != "excluded"}
        for t, row in dated.items():
            if row.strategy_exclusion is not None:
                validate_exclusion(row.strategy_exclusion, t, cutoff, row.available_at)
                if row.per is not None and row.per > 0:
                    raise DataQualityError("conflicting_strategy_exclusion")
                excluded.add(t)
        if ticker in excluded:
            reasons.append("negative_common_eps")
    def authoritative(row: ValuationRecord) -> bool:
        """Version 3 numeric inputs retain their original KRX provider identity."""
        if screening_version < 3:
            return True
        return row.provider in {"pykrx", "krx"}
    peer_pers = [r.per for t, r in dated.items() if ticker in membership and t in peers and authoritative(r) and
                 membership.get(t) == membership[ticker] and r.per is not None and r.per > 0
                 and (screening_version == 1 or r.status != "verified_not_applicable")]
    if any(t not in dated or (dated[t].per is None if screening_version == 1 else
           dated[t].status != "verified_not_applicable" and
           (dated[t].per is None or dated[t].per <= 0 or dated[t].status == "unknown"
            or not authoritative(dated[t]))) for t in peers - excluded):
        reasons.append("incomplete_sector_valuation")
    sector_median = median(peer_pers) if peer_pers else None
    own = dated.get(ticker)
    if own is None or own.per is None or own.per <= 0 or not authoritative(own) or sector_median is None:
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
        if ret > maximum_return_20 + 1e-12:
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
