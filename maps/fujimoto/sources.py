"""Bounded DART annual ingestion and observed, after-close market screening."""
from __future__ import annotations

import calendar
import re
import base64
import json
import zlib
from dataclasses import asdict
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from urllib.parse import urlparse

from sqlalchemy import select

from maps.common.exceptions import DataQualityError
from maps.common.models import (DartFilingReceipt, DartFinancialSnapshot, FujimotoEvidence,
                                SecurityFundamental, SecurityMetadata)
from maps.common.settings import get_settings
from maps.data.dart_financials import (DartClient, DartStop, DartFinancialRepository,
                                      available_date, REPORT_MONTH, KST)
from maps.data.classifications import ClassificationRepository
from maps.data.ohlcv_repo import HistoricalOHLCVRepository
from maps.data_quality.universe_filter import DataQualityFilter
from maps.fujimoto.repository import FujimotoRepository, fingerprint, json_data, utc_naive, money
from maps.fujimoto.replay import screening_evidence


def availability(publication: date, seen: datetime) -> datetime:
    """First eligible KRX session midnight in UTC, never a fiscal-year backdate."""
    return datetime.combine(available_date(publication, seen), time.min, KST).astimezone(timezone.utc)


def annual_dps(rows: list[dict], receipt: str, year: int) -> Decimal:
    """Accept exactly one annual common-share cash-DPS row from the same filing."""
    matched = [r for r in rows if r.get("stock_knd") == "보통주"
        and str(r.get("se", "")).replace(" ", "") == "주당현금배당금(원)"
        and r.get("rcept_no") == receipt
        and r.get("stlm_dt", "").replace(".", "-") == f"{year}-12-31"]
    if len(matched) != 1:
        raise DataQualityError("annual_common_dps_receipt_or_period_missing")
    return money(str(matched[0].get("thstrm", "")).replace(",", ""))


def record_comparability(db, ticker: str, receipts: list[str], *, source_url: str,
                         document_hash: str, explanation: str, owner: int | None,
                         now: datetime | None = None):
    """Record operator-reviewed corporate-action unit evidence at actual receipt time.

    Exact receipt list prevents applying an old review to a later revision. A URL,
    document SHA256 and substantive rationale are audit facts, never a pass flag.
    """
    if (urlparse(source_url).scheme != "https" or not urlparse(source_url).netloc
            or not re.fullmatch(r"[0-9a-f]{64}", document_hash)
            or len(explanation.strip()) < 20 or len(set(receipts)) < 3):
        raise DataQualityError("comparability_provenance_required")
    now = now or datetime.now(timezone.utc)
    sources = {r.payload["receipt"]: r for r in FujimotoRepository(db).evidence_as_of(
        "annual_source", ticker, now) if r.payload.get("receipt") in receipts}
    if set(sources) != set(receipts):
        raise DataQualityError("recorded_annual_receipts_required")
    payload = {"receipts": sorted(set(receipts)), "source_url": source_url,
        "document_hash": document_hash, "explanation": explanation, "reviewed_by": owner,
        "annual_source_ids": [sources[r].id for r in sorted(sources)],
        "periods": [sources[r].payload["period_end"] for r in sorted(sources)]}
    payload["share_basis"] = fingerprint(payload)
    row = FujimotoRepository(db).record_evidence("comparability", ticker, now,
        availability(now.astimezone(KST).date(), now), payload)
    db.commit()
    return row


class AnnualCollector:
    """Resumable all-market queue; shared DART pacing/request/time budgets apply."""
    def __init__(self, db, api_key: str, *, client=None, now=None):
        self.db, self.api_key, self.client = db, api_key, client
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.repo, self.financials = FujimotoRepository(db), DartFinancialRepository(db)

    def collect(self, ref_date: date, *, batch: int = 20) -> dict:
        """Journal revisions first, then fetch exact latest financial/dividend receipts."""
        summary = {"collected": 0, "partial": 0, "requests": 0, "status": "success"}
        if not self.api_key:
            return {**summary, "status": "insufficient", "reason": "missing_dart_api_key"}
        client = self.client or DartClient(self.api_key)
        # Current ingestion universe is never reused as historical membership.
        tickers = [r.ticker for r in self.db.query(SecurityMetadata).filter(
            SecurityMetadata.market.in_(("KOSPI", "KOSDAQ")), SecurityMetadata.security_type == "STOCK")]
        checked = {r.ticker: r.observed_at for r in self.db.query(FujimotoEvidence).filter_by(
            kind="annual_collection").order_by(FujimotoEvidence.id)}
        tickers.sort(key=lambda t: (checked.get(t, datetime.min), t))
        try:
            mapping = client.corp_codes()
            for ticker in tickers[:batch]:
                errors = []
                try:
                    if ticker not in mapping:
                        raise ValueError("corp_code_missing")
                    filings, page = {}, 1
                    while True:
                        response = client.request("list.json", corp_code=mapping[ticker],
                            bgn_de=(ref_date - timedelta(days=1500)).strftime("%Y%m%d"),
                            end_de=ref_date.strftime("%Y%m%d"), pblntf_ty="A", last_reprt_at="N",
                            page_no=page, page_count=100, sort="date", sort_mth="desc")
                        for row in response.get("list", []):
                            match = re.search(r"(사업|반기|분기)보고서\s*\((\d{4})\.(\d{2})\)", row.get("report_nm", ""))
                            if not match:
                                continue
                            kind, year, month = match[1], int(match[2]), int(match[3])
                            report = next((code for code, m in REPORT_MONTH.items() if m == month), None)
                            if (report is None or (kind == "사업" and month != 12)
                                    or (kind == "반기" and month != 6) or (kind == "분기" and month not in (3, 9))):
                                continue
                            seen = self.now()
                            filing = self.financials.record_receipt(ticker, row["rcept_no"],
                                date(year, month, calendar.monthrange(year, month)[1]),
                                datetime.strptime(row["rcept_dt"], "%Y%m%d").date(), seen)
                            if filing.period_end not in filings:
                                filings[filing.period_end] = filing, year, report
                        self.db.commit()  # known revisions mask old values even if budget expires
                        if response.get("status") == "013" or page >= int(response.get("total_page", 1)):
                            break
                        page += 1
                    if not filings:
                        errors.append("annual_filings_missing")
                    for filing, year, report in filings.values():
                        if report != "11011" and (ref_date - filing.period_end).days > 400:
                            continue
                        try:
                            snapshot = self.db.scalar(select(DartFinancialSnapshot).where(
                                DartFinancialSnapshot.ticker == ticker, DartFinancialSnapshot.receipt == filing.receipt))
                            if snapshot is None:
                                basis = "CFS"
                                financial = client.request("fnlttSinglAcntAll.json", corp_code=mapping[ticker],
                                    bsns_year=str(year), reprt_code=report, fs_div=basis)
                                if financial.get("status") == "013":
                                    basis = "OFS"
                                    financial = client.request("fnlttSinglAcntAll.json", corp_code=mapping[ticker],
                                        bsns_year=str(year), reprt_code=report, fs_div=basis)
                                snapshot = self.financials.save(ticker, filing.receipt, basis, year, report, financial, self.now())
                                self.db.commit()
                            if report != "11011":
                                continue
                            old = self.repo.evidence_as_of("annual_source", ticker, datetime.max)
                            if any(r.payload.get("receipt") == filing.receipt for r in old):
                                continue
                            dividend = client.request("alotMatter.json", corp_code=mapping[ticker],
                                bsns_year=str(year), reprt_code="11011")
                            dps = annual_dps(dividend.get("list", []), filing.receipt, year)
                            seen = self.now()
                            self.repo.record_evidence("annual_source", ticker, seen,
                                availability(filing.publication_date, seen), {
                                    "receipt": filing.receipt, "period_end": filing.period_end,
                                    "dividend_per_share": dps, "report_code": "11011",
                                    "financial_snapshot_id": snapshot.id, "dividend_response": dividend,
                                    "dividend_hash": fingerprint(dividend)})
                            self.db.commit()
                        except (ValueError, DataQualityError) as exc:
                            errors.append(str(exc))
                except (ValueError, DataQualityError, DartStop) as exc:
                    errors.append(str(exc))
                    if isinstance(exc, DartStop):
                        summary["status"] = "partial"
                now = self.now()
                self.repo.record_evidence("annual_collection", ticker, now, now,
                    {"errors": errors, "status": "partial" if errors else "collected",
                     "comparability": "requires_source_review"})
                self.db.commit()
                summary["partial" if errors else "collected"] += 1
                if summary["status"] == "partial":
                    break
        except (DartStop, ValueError) as exc:
            summary.update(status="partial", reason=str(exc))
        summary["requests"] = client.requests
        if summary["partial"]:
            summary["status"] = "partial"
        return summary


def capture_universe(db, securities: list, ref_date: date, *, metadata_quality: dict,
                     now: datetime | None = None) -> dict:
    """Freeze actual collected ordinary-share eligibility; no historical reconstruction."""
    now = now or datetime.now(timezone.utc)
    if ref_date != now.astimezone(KST).date() or now.astimezone(KST).time() < time(15, 30):
        raise DataQualityError("universe_requires_current_completed_session")
    result = DataQualityFilter(db, mode="live").generate(ref_date, securities)
    allowed = {s.ticker for s in result.universe}
    ready = metadata_quality.get("status") == "complete"
    payload = {"ref_date": ref_date, "metadata_quality": metadata_quality,
        "members": [{"ticker": s.ticker, "market": s.market, "name": s.name,
            "eligible": ready and s.ticker in allowed and s.market in {"KOSPI", "KOSDAQ"}
                and s.security_type == "STOCK" and not s.name.rstrip().endswith(("우", "우B", "우C"))}
            for s in securities], "rejections": result.rejected}
    FujimotoRepository(db).record_evidence("universe", "*", now, now, payload)
    db.commit()
    return payload


def financial_inputs(db, ticker: str, cutoff: date, now: datetime) -> tuple[list, list]:
    """Pending latest receipts mask earlier annual and quarterly parsed values."""
    repo = FujimotoRepository(db)
    sources = repo.evidence_as_of("annual_source", ticker, now)
    reviews = repo.evidence_as_of("comparability", ticker, now)
    annual, financial = [], []
    filings = db.query(DartFilingReceipt).filter(DartFilingReceipt.ticker == ticker,
        DartFilingReceipt.available_date <= cutoff).all()
    for filing in filings:
        snapshots = db.query(DartFinancialSnapshot).filter_by(ticker=ticker, receipt=filing.receipt).filter(
            DartFinancialSnapshot.available_date <= cutoff).order_by(DartFinancialSnapshot.basis,
            DartFinancialSnapshot.first_collected_at.desc()).all()
        snap = snapshots[0] if snapshots else None
        common = dict(ticker=ticker, period_end=filing.period_end, receipt=filing.receipt,
            publication_date=filing.publication_date, first_observed_at=snap.first_collected_at if snap else filing.first_collected_at,
            available_date=snap.available_date if snap else filing.available_date,
            basis=snap.basis if snap else "CFS", currency=snap.currency if snap else "KRW",
            revenue=float(snap.revenue) if snap else None, operating_profit=float(snap.operating_profit) if snap else None)
        financial.append({**common, "prior_revenue": float(snap.prior_revenue) if snap else None,
                          "prior_operating_profit": float(snap.prior_operating_profit) if snap else None})
        if filing.period_end.month != 12:
            continue
        source = next((r for r in reversed(sources) if r.payload["receipt"] == filing.receipt), None)
        review = next((r for r in reversed(reviews) if filing.receipt in r.payload["receipts"]), None)
        seen = max([utc_naive(common["first_observed_at"])] +
                   [r.observed_at for r in (source, review) if r is not None])
        annual.append({**common, "first_observed_at": seen,
            "available_date": available_date(filing.publication_date, seen),
            "share_basis": review.payload["share_basis"] if review else None,
            "dividend_per_share": source.payload["dividend_per_share"] if source else None})
    return json_data(annual), json_data(financial)


def screen_market(db, ref_date: date, *, now: datetime | None = None) -> dict:
    """Screen actual all-market snapshot with causal repositories and persist raw inputs."""
    now = now or datetime.now(timezone.utc)
    if ref_date != now.astimezone(KST).date() or now.astimezone(KST).time() < time(15, 30):
        raise DataQualityError("screen_requires_current_completed_session")
    repo = FujimotoRepository(db)
    universes = [r for r in repo.evidence_as_of("universe", "*", now)
                 if r.payload["ref_date"] == ref_date.isoformat()]
    if not universes:
        payload = {"ref_date": ref_date, "selected": 0, "reason": "missing_observed_universe", "ranked": []}
        repo.record_evidence("screen", "*", now, now, payload)
        db.commit()
        return payload
    universe = universes[-1]
    classifications = ClassificationRepository(db)
    sector = classifications.latest("sector", ref_date, now)
    members = classifications.memberships(sector) if sector else {}
    sectors = ({"ref_date": ref_date, "available_at": sector.published_at,
                "memberships": [(t, next(iter(s))) for t, s in sorted(members.items()) if len(s) == 1]}
               if members and all(len(s) == 1 for s in members.values()) else None)
    vals = [{"ticker": v.ticker, "ref_date": v.date, "available_at": v.updated_at, "per": v.per}
        for v in db.query(SecurityFundamental).filter(SecurityFundamental.date == ref_date,
                                                     SecurityFundamental.updated_at <= utc_naive(now))]
    history = HistoricalOHLCVRepository(db)
    ranked, count = [], 0
    remaining = max(0, get_settings().maps_fujimoto_candidate_rows -
                    db.query(FujimotoEvidence).filter_by(kind="candidate").count())
    if remaining < len(universe.payload["members"]):
        payload = {"ref_date": ref_date, "selected": 0, "ranked": [], "reason": "candidate_capacity_exhausted"}
        repo.record_evidence("screen", "*", now, now, payload)
        db.commit()
        return payload
    for member in universe.payload["members"]:
        ticker = member["ticker"]
        frame = history.to_dataframe(ticker, start=ref_date - timedelta(days=900), end=ref_date)
        annual, financial = financial_inputs(db, ticker, ref_date, now)
        # Same close*volume definition as HistoricalOHLCVRepository.avg_turnover_20d.
        prices = [{"date": index.date(), **{k: row[k] for k in ("open", "high", "low", "close", "volume")},
                   "turnover": row["close"] * row["volume"]} for index, row in frame.iterrows()]
        raw = json_data({"annual_records": annual, "financial_records": financial,
            "sectors": sectors, "valuations": vals, "prices": prices, "eligible": member["eligible"]})
        if prices:
            rule = screening_evidence(raw, ticker, ref_date, closed_dates=tuple(get_settings().krx_closed_dates))
        else:
            from maps.fujimoto.domain import RuleEvidence
            rule = RuleEvidence(ref_date, None, blocking_reasons=("missing_price_data",))
        compressed = base64.b64encode(zlib.compress(json.dumps(raw, separators=(",", ":"), allow_nan=False).encode())).decode()
        payload = {"ref_date": ref_date, "raw_zlib": compressed, "rule": rule,
                   "universe_id": universe.id, "sector_run_id": sector.id if sector else None}
        repo.record_evidence("candidate", ticker, now, now, payload)
        if rule.selection_passed:
            ranked.append((ticker, sum(p["turnover"] for p in prices[-20:]) / 20))
        count += 1
    ranked.sort(key=lambda x: (-x[1], x[0]))
    payload = {"ref_date": ref_date, "screened": count, "selected": len(ranked),
               "ranked": [t for t, _ in ranked], "universe_id": universe.id}
    repo.record_evidence("screen", "*", now, now, payload)
    db.commit()
    return payload


def current_financial_status(db, ticker: str, cutoff: date) -> str:
    """First available DART revision can trigger an owned exit before nightly screening."""
    values = DartFinancialRepository(db).get_as_of(ticker, cutoff)
    if values.get("reason") or any(values.get(k) is None for k in
            ("revenue", "prior_revenue", "operating_profit", "prior_operating_profit")):
        return "missing"
    return "deteriorated" if (values["revenue"] < values["prior_revenue"] or
        values["operating_profit"] < values["prior_operating_profit"] or
        values["operating_profit"] < 0) else "maintained"
