"""Bounded DART ingestion and strictly offline, first-seen as-of financial inputs."""
from __future__ import annotations

import calendar
import hashlib
import io
import json
import re
import time
import zipfile
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree

import requests
from sqlalchemy import select

from maps.common.models import CandidateSnapshot, DartCollectionState, DartFilingReceipt, DartFinancialSnapshot
from maps.common.settings import get_settings
from maps.market.trading_rules import is_krx_closed_date, trading_days_ago

KST = timezone(timedelta(hours=9))
REPORT_MONTH = {"11013": 3, "11012": 6, "11014": 9, "11011": 12}


def available_date(publication: date, seen: datetime) -> date:
    seen = seen.replace(tzinfo=timezone.utc) if seen.tzinfo is None else seen
    result = max(publication, seen.astimezone(KST).date()) + timedelta(days=1)
    while is_krx_closed_date(result, extra_closed_dates=get_settings().krx_closed_dates):
        result += timedelta(days=1)
    return result


def parse_financials(rows: list[dict], receipt: str, basis: str, year: int, report: str) -> dict:
    """One unambiguous IS (or CIS) pair, on the same cumulative period."""
    aliases = (("ifrs-full_Revenue", {"매출액", "수익(매출액)", "영업수익"}),
               ("dart_OperatingIncomeLoss", {"영업이익", "영업이익(손실)"}))
    for statement in ("IS", "CIS"):
        candidates = [r for r in rows if r.get("sj_div") == statement]
        pair = []
        for account, names in aliases:
            matches = [r for r in candidates if r.get("account_id") == account]
            if not matches:
                matches = [r for r in candidates if str(r.get("account_nm", "")).replace(" ", "") in names]
            if len(matches) > 1:
                raise ValueError("ambiguous_accounts")
            pair.extend(matches)
        if len(pair) != 2:
            continue
        for row in pair:
            if (row.get("rcept_no") != receipt or row.get("fs_div", basis) != basis
                    or str(row.get("bsns_year")) != str(year) or row.get("reprt_code") != report):
                raise ValueError("receipt_or_period_mismatch")
        if not pair[0].get("currency") or pair[0]["currency"] != pair[1].get("currency"):
            raise ValueError("currency_mismatch")
        if any(pair[0].get(field) != pair[1].get(field)
               for field in ("thstrm_nm", "frmtrm_nm", "thstrm_dt", "frmtrm_dt")):
            raise ValueError("period_label_mismatch")
        keys = ("thstrm_amount", "frmtrm_amount") if report == "11011" else ("thstrm_add_amount", "frmtrm_add_amount")
        try:
            amounts = [Decimal(str(row.get(key, "")).replace(",", "")) for row in pair for key in keys]
        except InvalidOperation as exc:
            raise ValueError("missing_amount") from exc
        if not all(value.is_finite() for value in amounts) or amounts[0] <= 0 or amounts[1] <= 0:
            raise ValueError("invalid_amount")
        month = REPORT_MONTH[report]
        return dict(zip(("revenue", "prior_revenue", "operating_profit", "prior_operating_profit"), amounts),
                    currency=pair[0]["currency"], period_end=date(year, month, calendar.monthrange(year, month)[1]))
    raise ValueError("missing_accounts")


class DartFinancialRepository:
    def __init__(self, db):
        self.db = db

    def record_receipt(self, ticker, receipt, period_end, publication_date, seen):
        existing = self.db.scalar(select(DartFilingReceipt).where(DartFilingReceipt.ticker == ticker, DartFilingReceipt.receipt == receipt))
        if existing is None:
            existing = DartFilingReceipt(ticker=ticker, receipt=receipt, period_end=period_end,
                publication_date=publication_date, first_collected_at=seen,
                available_date=available_date(publication_date, seen))
            self.db.add(existing)
            self.db.flush()
        return existing

    def save(self, ticker, receipt, basis, year, report, response, seen):
        parsed = parse_financials(response.get("list", []), receipt, basis, year, report)
        filing = self.db.scalar(select(DartFilingReceipt).where(DartFilingReceipt.ticker == ticker, DartFilingReceipt.receipt == receipt))
        if filing is None or filing.period_end != parsed["period_end"]:
            raise ValueError("unknown_receipt_or_period")
        raw_hash = hashlib.sha256(json.dumps(response, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        existing = self.db.scalar(select(DartFinancialSnapshot).where(DartFinancialSnapshot.ticker == ticker,
            DartFinancialSnapshot.receipt == receipt, DartFinancialSnapshot.basis == basis, DartFinancialSnapshot.raw_hash == raw_hash))
        if existing is None:
            existing = DartFinancialSnapshot(ticker=ticker, receipt=receipt, basis=basis, raw_hash=raw_hash,
                raw_response=response, publication_date=filing.publication_date, first_collected_at=seen,
                available_date=available_date(filing.publication_date, seen), **parsed)
            self.db.add(existing)
            self.db.flush()
        return existing

    def get_as_of(self, ticker: str, cutoff: date) -> dict:
        filing = self.db.scalar(select(DartFilingReceipt).where(DartFilingReceipt.ticker == ticker,
            DartFilingReceipt.available_date <= cutoff).order_by(DartFilingReceipt.period_end.desc(),
            DartFilingReceipt.publication_date.desc(), DartFilingReceipt.receipt.desc()).limit(1))
        missing = dict(revenue=None, prior_revenue=None, operating_profit=None, prior_operating_profit=None)
        if filing is None:
            return dict(missing, reason="unavailable", evidence={"source": "DART"})
        evidence = dict(source="DART", period_end=filing.period_end.isoformat(), receipt=filing.receipt,
                        publication_date=filing.publication_date.isoformat())
        if (cutoff - filing.period_end).days > 180:
            return dict(missing, reason="expired", evidence=evidence)
        snapshot = self.db.scalar(select(DartFinancialSnapshot).where(DartFinancialSnapshot.ticker == ticker,
            DartFinancialSnapshot.receipt == filing.receipt, DartFinancialSnapshot.available_date <= cutoff)
            .order_by(DartFinancialSnapshot.basis.asc(), DartFinancialSnapshot.first_collected_at.desc(), DartFinancialSnapshot.id.desc()).limit(1))
        if snapshot is None:
            return dict(missing, reason="pending_revision", evidence=evidence)
        evidence.update(basis=snapshot.basis, currency=snapshot.currency, available_date=snapshot.available_date.isoformat(),
                        first_collected_at=snapshot.first_collected_at.isoformat(), raw_hash=snapshot.raw_hash)
        return {**{key: getattr(snapshot, key) for key in missing}, "reason": None, "evidence": evidence}


class DartStop(RuntimeError):
    """Run-wide authentication, rate limit, or request/time budget stop."""


class DartClient:
    def __init__(self, api_key, *, get=requests.get, monotonic=time.monotonic, sleep=time.sleep):
        self.api_key, self.get, self.monotonic, self.sleep = api_key, get, monotonic, sleep
        self.started = monotonic()
        self.last_start = None
        self.requests = 0

    def request(self, endpoint, **params):
        for attempt in range(2):
            while True:
                now = self.monotonic()
                if self.requests >= 500 or now - self.started >= 900:
                    raise DartStop("budget_exhausted")
                wait = 0 if self.last_start is None else 1 - (now - self.last_start)
                if wait <= 0:
                    break
                self.sleep(wait)
            self.last_start = self.monotonic()
            self.requests += 1
            try:
                response = self.get("https://opendart.fss.or.kr/api/" + endpoint,
                    params={"crtfc_key": self.api_key, **params}, timeout=(3, 15))
                if response.status_code in (401, 403, 429):
                    raise DartStop("http_" + str(response.status_code))
                if response.status_code >= 500:
                    raise requests.ConnectionError("upstream")
                if response.status_code != 200:
                    raise ValueError("http_" + str(response.status_code))
                if endpoint == "corpCode.xml":
                    if response.content[:2] == b"PK":
                        return response.content
                    root = ElementTree.fromstring(response.content)
                    status = root.findtext("status")
                    if status in ("010", "011", "012", "020", "021", "100", "101"):
                        raise DartStop("dart_" + status)
                    raise ValueError("corp_mapping_unavailable")
                payload = response.json()
                status = payload.get("status")
                if status in ("010", "011", "012", "020", "021", "100", "101"):
                    raise DartStop("dart_" + status)
                if status in ("800", "900"):
                    raise requests.ConnectionError("upstream")
                if status not in ("000", "013"):
                    raise ValueError("dart_" + str(status))
                return payload
            except (requests.Timeout, requests.ConnectionError):
                if attempt:
                    raise ValueError("transient_exhausted") from None

    def corp_codes(self):
        with zipfile.ZipFile(io.BytesIO(self.request("corpCode.xml"))) as archive:
            root = ElementTree.fromstring(archive.read("CORPCODE.xml"))
        return {row.findtext("stock_code").strip(): row.findtext("corp_code")
                for row in root.iter("list") if (row.findtext("stock_code") or "").strip()}


class DartFinancialCollector:
    def __init__(self, db, api_key, *, client=None, now=lambda: datetime.now(timezone.utc)):
        self.db, self.api_key, self.client, self.now = db, api_key, client, now
        self.repo = DartFinancialRepository(db)

    def collect(self, ref_date: date) -> dict:
        summary = dict(status="success", collected=0, partial=0, requests=0)
        if not self.api_key:
            return {**summary, "status": "skipped", "reason": "missing_api_key"}
        client = self.client or DartClient(self.api_key)
        tickers = self.db.scalars(select(CandidateSnapshot.ticker).where(
            CandidateSnapshot.strategy_id == "contrarian_quality_accumulation_v1",
            CandidateSnapshot.ref_date >= trading_days_ago(ref_date, 19, extra_closed_dates=get_settings().krx_closed_dates),
            CandidateSnapshot.ref_date <= ref_date).distinct()).all()
        states = {s.ticker: s for s in self.db.scalars(select(DartCollectionState).where(DartCollectionState.ticker.in_(tickers)))}
        now = self.now().replace(tzinfo=None)
        tickers = [t for t in tickers if t not in states or states[t].status == "success"
                   or states[t].retry_at is None or states[t].retry_at <= now]
        tickers.sort(key=lambda t: (0 if t not in states else 2 if states[t].status == "success" else 1,
                                   (states[t].checked_at if t in states else None) or datetime.min))
        if not tickers:
            return summary
        try:
            mapping = client.corp_codes()
            for ticker in tickers:
                state = states.get(ticker)
                if state is None:
                    state = DartCollectionState(ticker=ticker)
                    self.db.add(state)
                state.checked_at = self.now().replace(tzinfo=None)
                try:
                    if ticker not in mapping:
                        raise ValueError("corp_code_missing")
                    filings = []
                    page = 1
                    # Journal each page immediately so a budget stop cannot hide a known correction.
                    while True:
                        payload = client.request("list.json", corp_code=mapping[ticker],
                            bgn_de=(ref_date - timedelta(days=400)).strftime("%Y%m%d"),
                            end_de=ref_date.strftime("%Y%m%d"), pblntf_ty="A", last_reprt_at="N",
                            page_no=page, page_count=100, sort="date", sort_mth="desc")
                        for row in payload.get("list", []):
                            match = re.search(r"(사업|반기|분기)보고서\s*\((\d{4})\.(\d{2})\)", row.get("report_nm", ""))
                            if not match:
                                continue
                            kind, year, month = match.group(1), int(match.group(2)), int(match.group(3))
                            report = next((code for code, m in REPORT_MONTH.items() if m == month), None)
                            if report is None or (kind == "사업" and month != 12) or (kind == "반기" and month != 6) or (kind == "분기" and month not in (3, 9)):
                                continue  # Non-calendar fiscal years need explicit period support.
                            filing = self.repo.record_receipt(ticker, row["rcept_no"], date(year, month, calendar.monthrange(year, month)[1]),
                                datetime.strptime(row["rcept_dt"], "%Y%m%d").date(), self.now())
                            filings.append((filing, year, report))
                        state.receipts = [f.receipt for f, _, _ in filings]
                        self.db.commit()
                        if payload.get("status") == "013" or page >= int(payload.get("total_page", 1)):
                            break
                        page += 1
                    errors = []
                    # Financial API exposes the latest revision, not a receipt parameter.
                    # Keep older receipts in the journal, but only request each period's latest.
                    latest = {}
                    for item in filings:
                        filing = item[0]
                        old = latest.get(filing.period_end)
                        if old is None or (filing.publication_date, filing.receipt) > (old[0].publication_date, old[0].receipt):
                            latest[filing.period_end] = item
                    for filing, year, report in latest.values():
                        if self.db.scalar(select(DartFinancialSnapshot.id).where(DartFinancialSnapshot.ticker == ticker,
                                DartFinancialSnapshot.receipt == filing.receipt).limit(1)):
                            continue
                        try:
                            basis = "CFS"
                            response = client.request("fnlttSinglAcntAll.json", corp_code=mapping[ticker], bsns_year=str(year), reprt_code=report, fs_div=basis)
                            if response.get("status") == "013":
                                basis = "OFS"
                                response = client.request("fnlttSinglAcntAll.json", corp_code=mapping[ticker], bsns_year=str(year), reprt_code=report, fs_div=basis)
                            self.repo.save(ticker, filing.receipt, basis, year, report, response, self.now())
                            self.db.commit()
                        except ValueError as exc:
                            errors.append(str(exc))
                    if errors:
                        raise ValueError(errors[0])
                    state.status, state.error = "success", None
                    state.retry_at = None  # Every daily run must discover new/corrected filings.
                    summary["collected"] += 1
                except (ValueError, DartStop) as exc:
                    state.status, state.error = "partial", str(exc)[:128]
                    state.retry_at = self.now().replace(tzinfo=None) + timedelta(hours=1)
                    summary["partial"] += 1
                    if isinstance(exc, DartStop):
                        raise
                finally:
                    self.db.commit()
        except DartStop as exc:
            summary.update(status="partial", reason=str(exc))
        except (ValueError, zipfile.BadZipFile, ElementTree.ParseError):
            summary.update(status="partial", reason="corp_mapping_unavailable")
        if summary["partial"]:
            summary["status"] = "partial"
        summary["requests"] = client.requests
        return summary
