"""Bounded DART annual ingestion and observed, after-close market screening."""
from __future__ import annotations

import calendar
import re
import base64
import json
import zlib
from math import isfinite
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
from maps.fujimoto.domain import RuleEvidence


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


def record_valuation_source(db, ticker: str, ref_date: date, *, per: float | None,
                            raw: dict, source_url: str, document_hash: str):
    """Append an exact-date raw observation at actual receipt time; never alter fundamentals."""
    from maps.fujimoto.evidence import ValuationRecord
    now = datetime.now(timezone.utc)
    ref_date = date.fromisoformat(ref_date) if isinstance(ref_date, str) else ref_date
    payload = json_data(dict(ticker=ticker, ref_date=ref_date, per=per, raw=raw,
        raw_hash=fingerprint(raw), source_url=source_url, document_hash=document_hash, observed_at=now))
    if not isinstance(raw, dict) or not raw or ref_date > now.astimezone(KST).date():
        raise DataQualityError("invalid_valuation_provenance")
    ValuationRecord(ticker, ref_date, now, per, "numeric" if per is not None else "unknown",
                    {"source": payload})
    row = FujimotoRepository(db).record_evidence("valuation_source", ticker, now, now, payload)
    db.commit()
    return row


def record_valuation_review(db, ticker: str, ref_date: date, *, source_id: int,
                            contract_url: str, contract_hash: str, explanation: str,
                            owner: int | None):
    """Bind official non-applicability semantics to one immutable actual source revision."""
    from maps.fujimoto.evidence import ValuationRecord
    now = datetime.now(timezone.utc)
    ref_date = date.fromisoformat(ref_date) if isinstance(ref_date, str) else ref_date
    repo = FujimotoRepository(db)
    source = next((r for r in repo.evidence_as_of("valuation_source", ticker, now) if r.id == source_id), None)
    if source is None:
        raise DataQualityError("recorded_valuation_source_required")
    payload = json_data(dict(ticker=ticker, ref_date=ref_date, source_id=source_id,
        source_hash=fingerprint(source.payload), contract_url=contract_url, contract_hash=contract_hash,
        explanation=explanation, reviewed_by=owner, observed_at=now))
    ValuationRecord(ticker, ref_date, now, source.payload["per"], "verified_not_applicable",
        {"source_id": source.id, "source": source.payload, "review": payload})
    row = repo.record_evidence("valuation_review", ticker, now, now, payload)
    db.commit()
    return row


def valuation_inputs(db, ref_date: date, now: datetime, fundamentals: list[dict]) -> list[dict]:
    """Latest observation wins; a review applies only to its exact source, never a newer value."""
    repo = FujimotoRepository(db)
    values = {r["ticker"]: {**r, "status": "numeric" if r["per"] is not None else "unknown"}
              for r in fundamentals if r.get("provider", "pykrx") in {"pykrx", "krx"}}
    sources = db.query(FujimotoEvidence).filter_by(kind="valuation_source").order_by(FujimotoEvidence.id)
    for source in sources:
        host = urlparse(source.payload.get("source_url", "")).hostname or ""
        krx = host == "krx.co.kr" or host.endswith(".krx.co.kr")
        fss = host == "fss.or.kr" or host.endswith(".fss.or.kr")
        if not krx and not fss:
            continue  # Diagnostic providers never replace authoritative KRX inputs.
        authoritative = values.get(source.ticker)
        if not krx and (source.payload.get("per") is not None and source.payload["per"] > 0
                        or authoritative and authoritative.get("per") is not None and authoritative["per"] > 0):
            continue
        if (source.payload.get("ref_date") != ref_date.isoformat()
                or source.observed_at > utc_naive(now) or source.available_at > utc_naive(now)):
            continue
        old = values.get(source.ticker)
        if old:
            previous = old.get("provenance", {}).get("source", {}).get("observed_at", old["available_at"])
            previous = datetime.fromisoformat(previous) if isinstance(previous, str) else previous
            if utc_naive(previous) > source.observed_at:
                continue
        reviews = [r for r in repo.evidence_as_of("valuation_review", source.ticker, now)
                   if r.payload.get("source_id") == source.id]
        review = reviews[-1] if reviews else None
        values[source.ticker] = dict(ticker=source.ticker, ref_date=ref_date,
            available_at=max(source.observed_at, review.observed_at if review else source.observed_at),
            per=source.payload["per"], status="verified_not_applicable" if review else
                ("numeric" if source.payload["per"] is not None else "unknown"),
            provider="krx" if krx else "fss",
            provenance={"source_id": source.id, "source": source.payload,
                        **({"review": review.payload} if review else {})})
    return json_data(list(values.values()))


class AnnualCollector:
    """Resumable all-market queue; shared DART pacing/request/time budgets apply."""
    def __init__(self, db, api_key: str, *, client=None, now=None):
        self.db, self.api_key, self.client = db, api_key, client
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.repo, self.financials = FujimotoRepository(db), DartFinancialRepository(db)

    def collect(self, ref_date: date, *, batch: int = 20, tickers: list[str] | None = None) -> dict:
        """Journal revisions first, then fetch exact latest financial/dividend receipts."""
        summary = {"collected": 0, "partial": 0, "requests": 0, "status": "success",
                   "attempted": [], "rejected_targets": [], "transient": 0, "missing_source": 0}
        client = self.client or DartClient(self.api_key)
        # Current ingestion universe is never reused as historical membership.
        targets = tickers
        tickers = [r.ticker for r in self.db.query(SecurityMetadata).filter(
            SecurityMetadata.market.in_(("KOSPI", "KOSDAQ")), SecurityMetadata.security_type == "STOCK")]
        priorities = {}
        if targets is not None:
            universes = self.repo.evidence_as_of("universe", "*", self.now())
            latest = max(universes, key=lambda r: (r.payload.get("ref_date", ""), r.observed_at, r.id)) if universes else None
            allowed = {m["ticker"] for m in latest.payload["members"] if m.get("eligible") is True} if latest else set()
            summary["rejected_targets"] = sorted(set(targets) - allowed)
            tickers = sorted(set(targets) & allowed)
            cutoff = utc_naive(self.now())
            candidates = self.db.query(FujimotoEvidence.ticker,
                FujimotoEvidence.payload["rule"]["blocking_reasons"]).filter(
                    FujimotoEvidence.kind == "candidate", FujimotoEvidence.ticker.in_(tickers),
                    FujimotoEvidence.observed_at <= cutoff, FujimotoEvidence.available_at <= cutoff,
                ).order_by(FujimotoEvidence.id).yield_per(100)
            for candidate_ticker, reasons in candidates:
                priorities[candidate_ticker] = bool(set(reasons or []) & {
                    "annual_growth_failed", "dividend_failed", "ineligible_security"})
        checked = {r.ticker: r.observed_at for r in self.db.query(FujimotoEvidence).filter_by(
            kind="annual_collection").order_by(FujimotoEvidence.id)}
        tickers.sort(key=lambda t: (priorities.get(t, False), checked.get(t, datetime.min), t))
        if not self.api_key:
            return {**summary, "status": "insufficient", "reason": "missing_dart_api_key"}
        try:
            mapping = client.corp_codes() if tickers else {}
            for ticker in tickers[:max(0, min(batch, 500))]:
                summary["attempted"].append(ticker)
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
                categories = ["transient" if e == "transient_exhausted" or e == "budget_exhausted"
                              or e.startswith(("http_429", "http_5", "dart_020", "dart_021", "dart_800", "dart_900"))
                              else "missing_source" for e in errors]
                for category in set(categories):
                    summary[category] += 1
                self.repo.record_evidence("annual_collection", ticker, now, now,
                    {"errors": errors, "error_categories": categories, "status": "partial" if errors else "collected",
                     "comparability": "requires_source_review"})
                self.db.commit()
                summary["partial" if errors else "collected"] += 1
                if summary["status"] == "partial":
                    break
        except (DartStop, ValueError) as exc:
            category = "transient" if str(exc) in {"budget_exhausted", "transient_exhausted"} or str(exc).startswith(
                ("http_429", "http_5", "dart_020", "dart_021", "dart_800", "dart_900")) else "missing_source"
            summary.update(status="partial", reason=str(exc), error_category=category)
            summary[category] += 1
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


def _raw_numbers(value: object) -> object:
    """Keep nonfinite raw inputs as explicit strings that still fail rule validation."""
    if isinstance(value, dict):
        return {key: _raw_numbers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_raw_numbers(item) for item in value]
    return str(value) if isinstance(value, float) and not isfinite(value) else value


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
    vals = [{"ticker": v.ticker, "ref_date": v.date, "available_at": v.updated_at, "per": v.per,"provider":v.source}
        for v in db.query(SecurityFundamental).filter(SecurityFundamental.date == ref_date,
                                                     SecurityFundamental.updated_at <= utc_naive(now))]
    vals = valuation_inputs(db, ref_date, now, vals)
    from maps.fujimoto.valuation import security_inputs, exclusion_inputs, security_kind
    classifications = security_inputs(db, ref_date, now)
    exclusions = exclusion_inputs(db, ref_date, now)
    values = {v["ticker"]: v for v in vals}
    for ticker, proof in exclusions.items():
        value = values.get(ticker)
        if value is None or value["per"] is None or value["per"] <= 0:
            values[ticker] = {**(value or dict(ticker=ticker,ref_date=ref_date,per=None,status="unknown")),
                "available_at": now, "strategy_exclusion": proof}
    vals = json_data(list(values.values()))
    states = {}
    for member in universe.payload["members"]:
        ticker = member["ticker"]
        kind = security_kind(classifications.get(ticker),ticker,ref_date)
        value = values.get(ticker,{})
        state = ("excluded_security" if kind == "excluded" else "unknown_classification" if kind == "unknown"
                 else "numeric" if value.get("per") is not None and value["per"] > 0
                 else "negative_common_eps" if value.get("strategy_exclusion") else
                 "verified_not_applicable" if value.get("status") == "verified_not_applicable" else "unknown")
        states[state] = states.get(state,0) + 1
    history = HistoricalOHLCVRepository(db)
    ranked, count, blocked, blocking_reasons = [], 0, 0, {}
    v2_selected = 0
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
        sector_membership = dict(sectors["memberships"]) if sectors else {}
        peer_tickers = {t for t,s in sector_membership.items() if s == sector_membership.get(ticker)} | {ticker}
        frozen_sectors = ({**sectors,"memberships":[(t,s) for t,s in sectors["memberships"] if t in peer_tickers]}
                          if sectors and ticker in sector_membership else sectors)
        raw = _raw_numbers(json_data({"screening_version": 3, "annual_records": annual, "financial_records": financial,
            "sectors": frozen_sectors, "valuations": [v for v in vals if v["ticker"] in peer_tickers],
            "prices": prices, "eligible": member["eligible"],
            "security_classifications": {t:p for t,p in classifications.items() if t in peer_tickers}}))
        if prices:
            try:
                rule = screening_evidence(raw, ticker, ref_date, closed_dates=tuple(get_settings().krx_closed_dates))
                v2_selected += screening_evidence({**raw,"screening_version":2}, ticker, ref_date,
                    closed_dates=tuple(get_settings().krx_closed_dates)).selection_passed
            except DataQualityError as exc:
                rule = RuleEvidence(ref_date, None, blocking_reasons=(str(exc),))
        else:
            rule = RuleEvidence(ref_date, None, blocking_reasons=("missing_price_data",))
        if not rule.selection_passed:
            blocked += 1
        for reason in set(rule.blocking_reasons):
            blocking_reasons[reason] = blocking_reasons.get(reason, 0) + 1
        compressed = base64.b64encode(zlib.compress(json.dumps(raw, separators=(",", ":"), allow_nan=False).encode())).decode()
        payload = {"ref_date": ref_date, "raw_zlib": compressed, "rule": rule,
                   "universe_id": universe.id, "sector_run_id": sector.id if sector else None}
        repo.record_evidence("candidate", ticker, now, now, payload)
        if rule.selection_passed:
            ranked.append((ticker, sum(p["turnover"] for p in prices[-20:]) / 20))
        count += 1
    ranked.sort(key=lambda x: (-x[1], x[0]))
    payload = {"ref_date": ref_date, "screened": count, "selected": len(ranked),
               "blocked": blocked, "blocking_reasons": blocking_reasons,
               "screening_version": 3, "valuation_states": states,
               "comparison": {"v2_selected": v2_selected, "v3_selected":len(ranked)},
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
