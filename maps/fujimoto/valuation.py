"""Official membership and reviewed signed common EPS, independent of growth parsing.

DART negative EPS is a strategy exclusion, never KRX non-applicability or a PER.
Sources and decisions are separate immutable evidence; all proofs rebuild offline.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timezone
from math import isfinite
from decimal import Decimal, InvalidOperation
import re

from maps.common.exceptions import DataQualityError
from maps.common.models import DartFilingReceipt, FujimotoEvidence
from maps.data.dart_financials import DartClient, DartFinancialRepository, DartStop, KST
from maps.fujimoto.repository import FujimotoRepository, fingerprint, json_data, utc_naive

DIRECT = "ifrs-full_BasicEarningsLossPerShare"
CONTINUING = "ifrs-full_BasicEarningsLossPerShareFromContinuingOperations"
DISCONTINUED = "ifrs-full_BasicEarningsLossPerShareFromDiscontinuedOperations"
SECTORS = {"", "우량기업부", "중견기업부", "관리종목(소속부없음)", "일반기업부",
           "벤처기업부", "기술성장기업부", "외국기업(소속부없음)", "투자주의환기종목(소속부없음)"}
NONORDINARY = {"구형우선주", "신형우선주", "종류주권"}


def common_eps(rows: list[dict], ticker: str, corp_code: str, receipt: str, basis: str, year: int) -> Decimal:
    """Parse unambiguous signed annual basic common EPS; absent components aren't zero."""
    tags = {DIRECT, CONTINUING, DISCONTINUED}
    # Unrecognized EPS accounts can contradict standard totals; do not silently ignore them.
    candidates = [r for r in rows if r.get("account_id") in tags or
                  "주당" in str(r.get("account_nm", "")) or "EarningsLossPerShare" in str(r.get("account_id", ""))]
    candidates = [r for r in candidates if "희석" not in str(r.get("account_nm", ""))
                  and "Diluted" not in str(r.get("account_id", ""))]
    if not candidates:
        raise DataQualityError("missing_common_basic_eps")
    values, contexts = {}, set()
    for row in candidates:
        tag = row.get("account_id")
        detail = str(row.get("account_detail", "")).replace(" ", "")
        name = str(row.get("account_nm", "")).replace(" ", "")
        if "우선" in detail + name and "보통" not in detail + name:
            continue  # An explicitly separate preferred-share row isn't common EPS.
        if tag not in tags or "우선" in detail + name or "종류" in detail + name:
            raise DataQualityError("ambiguous_common_eps")
        # DART's standard unqualified basic EPS denotes common shares. Explicit
        # share contexts must identify common shares; custom dimensions need review.
        if detail not in {"", "-", "보통주", "보통주식", "연결재무제표", "별도재무제표"}:
            raise DataQualityError("ambiguous_common_eps")
        if (row.get("rcept_no") != receipt or row.get("fs_div", basis) != basis
                or str(row.get("bsns_year")) != str(year) or row.get("reprt_code") != "11011"
                or row.get("corp_code", corp_code) != corp_code or row.get("stock_code", ticker) != ticker
                or row.get("sj_div") not in {"IS", "CIS"} or not row.get("currency")
                or not row.get("thstrm_nm")):
            raise DataQualityError("eps_context_mismatch")
        period = row.get("thstrm_dt")
        if period and re.sub(r"[^0-9]", "", period) != f"{year}0101{year}1231":
            raise DataQualityError("eps_period_mismatch")
        context = tuple(str(row.get(k, "")) for k in ("sj_div", "currency", "thstrm_nm", "thstrm_dt", "account_detail"))
        contexts.add(context)
        try:
            amount = Decimal(str(row.get("thstrm_amount", "")).replace(",", ""))
        except InvalidOperation as exc:
            raise DataQualityError("missing_eps_amount") from exc
        if not amount.is_finite() or tag in values:
            raise DataQualityError("ambiguous_common_eps")
        values[tag] = amount
    if not values:
        raise DataQualityError("missing_common_basic_eps")
    if len(contexts) != 1:
        raise DataQualityError("eps_context_mismatch")
    components = CONTINUING in values or DISCONTINUED in values
    if components and not {CONTINUING, DISCONTINUED} <= values.keys():
        raise DataQualityError("missing_eps_component")
    total = values[CONTINUING] + values[DISCONTINUED] if components else values.get(DIRECT)
    if DIRECT in values and values[DIRECT] != total:
        raise DataQualityError("conflicting_common_eps")
    return total


def applicable_year(cutoff: date) -> int:
    """December annual cohort under the KRX May annual update window."""
    return cutoff.year - (1 if cutoff.month >= 5 else 2)


def _identity_matches(source: dict) -> bool:
    """Compare the same official identity across company.json and corpCode formats."""
    identity = source["identity"]
    if "status" in identity:
        return (identity.get("status") == "000" and identity.get("stock_code") == source["ticker"]
                and identity.get("corp_code") == source["corp_code"])
    return identity.get(source["ticker"]) == source["corp_code"]


def _record_identity_observation(db, ticker: str, identity: dict):
    """Journal official identity immediately, independently of later financial failures."""
    now = datetime.now(timezone.utc)
    if "status" in identity:
        corp = identity.get("corp_code") if identity.get("status") == "000" and identity.get("stock_code") == ticker else None
        url = "https://opendart.fss.or.kr/api/company.json"
    else:
        corp = identity.get(ticker)
        url = "https://opendart.fss.or.kr/api/corpCode.xml"
    corp = corp if isinstance(corp,str) and corp.strip() else None
    payload = json_data(dict(ticker=ticker,corp_code=corp,identity=identity,
        identity_hash=fingerprint(identity),identity_url=url,observed_at=now))
    latest = db.query(FujimotoEvidence).filter_by(kind="eps_identity",ticker=ticker).order_by(
        FujimotoEvidence.id.desc()).first()
    if latest is not None and latest.payload.get("corp_code") == corp:
        return latest
    row = FujimotoRepository(db).record_evidence("eps_identity",ticker,now,now,payload)
    db.commit()  # A subsequent list/statement error must not roll back known identity.
    return row


def _source_eps(source: dict) -> Decimal:
    """Validate exact receipt, basis and original response again on every rebuild."""
    raw = source["response"]
    identity, filing = source["identity"], source["filing"]
    if (source["raw_hash"] != fingerprint(raw) or raw.get("status") != "000"
            or source["identity_hash"] != fingerprint(identity) or not _identity_matches(source)
            or source["filing_hash"] != fingerprint(filing)
            or filing.get("corp_code") != source["corp_code"]
            or filing.get("stock_code",source["ticker"]) != source["ticker"]
            or filing.get("rcept_no") != source["receipt"]
            or filing.get("rcept_dt") != source["publication_date"].replace("-","")
            or not re.search(rf"사업보고서\s*\({source['year']}\.12\)",filing.get("report_nm",""))):
        raise DataQualityError("invalid_eps_source")
    if source["basis"] == "OFS":
        # 013 is merely no data, not proof that consolidated statements do not
        # apply. Keep OFS diagnostic until original-document basis review exists.
        raise DataQualityError("explicit_cfs_nonapplicability_required")
    elif source["basis"] != "CFS":
        raise DataQualityError("invalid_eps_basis")
    return common_eps(raw.get("list", []), source["ticker"], source["corp_code"],
                      source["receipt"], source["basis"], source["year"])


def record_eps_source(db, ticker: str, *, corp_code: str, receipt: str, year: int,
                      basis: str, publication_date: date, response: dict,
                      identity: dict, filing: dict, cfs_nonapplicability: dict | None = None):
    """Store a raw observation at actual ingestion time, including unresolved responses."""
    from maps.fujimoto.sources import availability
    now = datetime.now(timezone.utc)
    if (not ticker or not corp_code or not receipt or type(year) is not int
            or basis not in {"CFS", "OFS"} or publication_date > now.astimezone(KST).date()
            or date(year, 12, 31) > publication_date or not isinstance(response, dict)):
        raise DataQualityError("invalid_eps_source")
    payload = json_data(dict(ticker=ticker, corp_code=corp_code, receipt=receipt, year=year,
        period_end=date(year,12,31), basis=basis, publication_date=publication_date,
        response=response, raw_hash=fingerprint(response), observed_at=now,
        identity=identity,identity_hash=fingerprint(identity),identity_url=("https://opendart.fss.or.kr/api/company.json"
            if identity.get("status") == "000" else "https://opendart.fss.or.kr/api/corpCode.xml"),
        filing=filing,filing_hash=fingerprint(filing),filing_url="https://opendart.fss.or.kr/api/list.json",
        cfs_nonapplicability=cfs_nonapplicability,
        cfs_hash=fingerprint(cfs_nonapplicability) if cfs_nonapplicability is not None else None))
    repo = FujimotoRepository(db)
    # Archived identity may seed initial context, but cannot supersede an
    # independently observed corporation merely because the archive is imported.
    if db.query(FujimotoEvidence.id).filter_by(kind="eps_identity",ticker=ticker).first() is None:
        _record_identity_observation(db,ticker,identity)
    latest = db.query(FujimotoEvidence).filter_by(kind="eps_source",ticker=ticker).order_by(
        FujimotoEvidence.id.desc()).first()
    if latest is not None:
        observation_fields = {"observed_at","identity","identity_hash","identity_url"}
        previous = {k:v for k,v in latest.payload.items() if k not in observation_fields}
        current = {k:v for k,v in payload.items() if k not in observation_fields}
        if (_identity_matches(payload) and _identity_matches(latest.payload)
                and fingerprint(previous) == fingerprint(current)):
            return latest  # Preserve first observation/review; checks are journalled separately.
    DartFinancialRepository(db).record_receipt(ticker, receipt, date(year,12,31), publication_date, now)
    row = repo.record_evidence("eps_source", ticker, now, availability(publication_date,now), payload)
    db.commit()
    return row


def review_eps_source(db, source_id: int, *, owner: int | None, explanation: str):
    """Apply a validated negative common EPS decision to one immutable source hash."""
    from maps.fujimoto.sources import availability
    now = datetime.now(timezone.utc)
    repo = FujimotoRepository(db)
    source = db.query(FujimotoEvidence).filter_by(id=source_id, kind="eps_source").one_or_none()
    if source is None or len(explanation.strip()) < 20:
        raise DataQualityError("recorded_eps_source_and_review_required")
    if _source_eps(source.payload) >= 0:
        raise DataQualityError("negative_common_eps_required")
    payload = json_data(dict(ticker=source.ticker, source_id=source.id, source_hash=fingerprint(source.payload),
        receipt=source.payload["receipt"], explanation=explanation, reviewed_by=owner, observed_at=now))
    row = repo.record_evidence("eps_review", source.ticker, now,
        availability(date.fromisoformat(source.payload["publication_date"]), now), payload)
    db.commit()
    return row


def validate_exclusion(proof: dict, ticker: str, cutoff: date, available_at: datetime) -> None:
    """Frozen strategy evidence uses the same receipt, hash and causal rules everywhere."""
    from maps.fujimoto.sources import availability
    try:
        source, review = proof["source"], proof["review"]
        observed = datetime.fromisoformat(source["observed_at"])
        reviewed = datetime.fromisoformat(review["observed_at"])
        publication = date.fromisoformat(source["publication_date"])
        ready = availability(publication, reviewed)
        known = proof["known_receipts"]
        identity = proof["identity_observation"]["source"]
        identity_observed = datetime.fromisoformat(identity["observed_at"])
        identity_url = ("https://opendart.fss.or.kr/api/company.json" if "status" in identity["identity"]
                        else "https://opendart.fss.or.kr/api/corpCode.xml")
        if (identity["ticker"] != ticker or identity["corp_code"] != source["corp_code"]
                or identity["identity_hash"] != fingerprint(identity["identity"])
                or identity["identity_url"] != identity_url or not _identity_matches(identity)
                or utc_naive(identity_observed) > utc_naive(available_at)
                or utc_naive(identity_observed) > utc_naive(datetime.now(timezone.utc))):
            raise ValueError("identity_context")
        if not known or any(r["ticker"] != ticker or r["period_end"] != source["period_end"]
                or utc_naive(datetime.fromisoformat(r["observed_at"])) > utc_naive(available_at) for r in known):
            raise ValueError("receipt_context")
        latest_receipt = max(known,key=lambda r:(r["publication_date"],r["receipt"]))["receipt"]
        if (source["ticker"] != ticker or review["ticker"] != ticker
                or source["year"] != applicable_year(cutoff)
                or source["period_end"] != f"{source['year']}-12-31"
                or proof["latest_receipt"] != source["receipt"]
                or latest_receipt != source["receipt"]
                or proof["corp_code"] != source["corp_code"]
                or review["receipt"] != source["receipt"] or review["source_id"] != proof["source_id"]
                or review["source_hash"] != fingerprint(source)
                or len(review["explanation"].strip()) < 20
                or utc_naive(observed) > utc_naive(reviewed)
                or utc_naive(reviewed) > utc_naive(datetime.now(timezone.utc))
                or utc_naive(ready) > utc_naive(available_at)
                or ready.astimezone(KST).date() > cutoff or publication < date(source["year"],12,31)
                or _source_eps(source) >= 0):
            raise ValueError("binding")
    except (KeyError, TypeError, ValueError, AttributeError, DataQualityError) as exc:
        raise DataQualityError("invalid_strategy_exclusion") from exc


def exclusion_inputs(db, cutoff: date, now: datetime) -> dict[str, dict]:
    """Latest known correction/identity/source masks old reviews, even while pending."""
    repo = FujimotoRepository(db)
    sources = db.query(FujimotoEvidence).filter_by(kind="eps_source").filter(
        FujimotoEvidence.observed_at <= utc_naive(now)).order_by(FujimotoEvidence.id).all()
    latest = {}
    for row in sources:
        if row.payload.get("year") == applicable_year(cutoff):
            latest[row.ticker] = row
    identities = {r.ticker:r for r in db.query(FujimotoEvidence).filter_by(kind="eps_identity").filter(
        FujimotoEvidence.observed_at <= utc_naive(now)).order_by(FujimotoEvidence.id)}
    result = {}
    for ticker, source in latest.items():
        filings = db.query(DartFilingReceipt).filter_by(ticker=ticker, period_end=date(applicable_year(cutoff),12,31)).filter(
            DartFilingReceipt.first_collected_at <= utc_naive(now)).all()
        filing = max(filings, key=lambda r:(r.publication_date,r.receipt)) if filings else None
        reviews = [r for r in repo.evidence_as_of("eps_review",ticker,now) if r.payload.get("source_id") == source.id]
        identity = identities.get(ticker)
        if filing is None or not reviews or identity is None:
            continue
        proof = dict(source_id=source.id, source=source.payload, review=reviews[-1].payload,
                     latest_receipt=filing.receipt, corp_code=source.payload["corp_code"],
                     identity_observation={"source_id":identity.id,"source":identity.payload},
                     known_receipts=json_data([dict(ticker=r.ticker,receipt=r.receipt,period_end=r.period_end,
                         publication_date=r.publication_date,observed_at=r.first_collected_at) for r in filings]))
        try:
            validate_exclusion(proof,ticker,cutoff,now)
        except DataQualityError:
            continue  # Unresolved evidence remains unknown, with its raw source retained.
        result[ticker] = proof
    return result


def validate_current_exclusions(db, snapshot: dict, cutoff: date, now: datetime) -> None:
    """Recheck only frozen EPS-exclusion dependencies before current BUY use.

    Two dependency-scoped queries retain the candidate's fiscal cohort. Numeric
    PER, metadata and prices remain frozen; historical replay never calls this.
    """
    if snapshot.get("screening_version",1) != 3:
        return
    try:
        dependencies = {}
        for row in snapshot["valuations"]:
            proof = row.get("strategy_exclusion")
            if proof is not None:
                if row["ref_date"] != cutoff.isoformat():
                    raise ValueError("valuation_date")
                validate_exclusion(proof,row["ticker"],cutoff,datetime.fromisoformat(row["available_at"]))
                dependencies[row["ticker"]] = proof
        if not dependencies:
            return
        sources,identities = {},{}
        for row in db.query(FujimotoEvidence).filter(
                FujimotoEvidence.kind.in_(("eps_source","eps_identity")),
                FujimotoEvidence.ticker.in_(dependencies),FujimotoEvidence.observed_at <= utc_naive(now)).order_by(FujimotoEvidence.id):
            if row.kind == "eps_identity":
                identities[row.ticker] = row
            elif row.payload.get("year") == applicable_year(cutoff):
                sources[row.ticker] = row
        receipts = {}
        for row in db.query(DartFilingReceipt).filter(DartFilingReceipt.ticker.in_(dependencies),
                DartFilingReceipt.period_end == date(applicable_year(cutoff),12,31),
                DartFilingReceipt.first_collected_at <= utc_naive(now)):
            old = receipts.get(row.ticker)
            if old is None or (row.publication_date,row.receipt) > (old.publication_date,old.receipt):
                receipts[row.ticker] = row
        for ticker,proof in dependencies.items():
            source,identity,receipt = sources.get(ticker),identities.get(ticker),receipts.get(ticker)
            if (source is None or identity is None or receipt is None
                    or source.id != proof["source_id"] or source.fingerprint != fingerprint(source.payload)
                    or source.fingerprint != fingerprint(proof["source"])
                    or identity.id != proof["identity_observation"]["source_id"]
                    or identity.fingerprint != fingerprint(identity.payload)
                    or identity.fingerprint != fingerprint(proof["identity_observation"]["source"])
                    or receipt.receipt != proof["source"]["receipt"]
                    or receipt.publication_date.isoformat() != proof["source"]["publication_date"]):
                raise ValueError("superseded")
    except (KeyError,TypeError,ValueError,AttributeError,DataQualityError) as exc:
        raise DataQualityError("superseded_strategy_exclusion") from exc


def record_security_source(db, ref_date: date, raw: dict):
    """Current official KRX basic metadata cannot be relabelled as historical."""
    now = datetime.now(timezone.utc)
    if ref_date != now.astimezone(KST).date() or not isinstance(raw,dict) or not raw.get("ISU_SRT_CD"):
        raise DataQualityError("current_official_metadata_required")
    payload = json_data(dict(ticker=raw["ISU_SRT_CD"],ref_date=ref_date,observed_at=now,
        raw=raw,raw_hash=fingerprint(raw),source_url="https://data.krx.co.kr/contents/MDC/MDI/mdiLoader"))
    row = FujimotoRepository(db).record_evidence("security_source",raw["ISU_SRT_CD"],now,now,payload)
    db.commit()
    return row


def security_kind(proof: dict | None, ticker: str, cutoff: date) -> str:
    """Classify only exact-date official fields; never ticker or company names."""
    if proof is None:
        return "unknown"
    try:
        raw = proof["raw"]
        observed = datetime.fromisoformat(proof["observed_at"])
        if (proof["ticker"] != ticker or raw["ISU_SRT_CD"] != ticker
                or proof["ref_date"] != cutoff.isoformat() or proof["raw_hash"] != fingerprint(raw)
                or observed.astimezone(KST).date() != cutoff
                or utc_naive(observed) > utc_naive(datetime.now(timezone.utc))
                or proof["source_url"] != "https://data.krx.co.kr/contents/MDC/MDI/mdiLoader"):
            raise ValueError("binding")
        if not {"KIND_STKCERT_TP_NM","SECT_TP_NM"} <= raw.keys():
            return "unknown"
        share, sector = raw["KIND_STKCERT_TP_NM"], raw["SECT_TP_NM"]
        if "MKT_TP_NM" not in raw or "SECUGRP_NM" not in raw:
            return "unknown"
        if raw["MKT_TP_NM"] not in {"KOSPI","KOSDAQ","KOSDAQ GLOBAL","KONEX"} or raw["SECUGRP_NM"] != "주권":
            return "unknown"
        if raw["MKT_TP_NM"] == "KONEX":
            return "excluded"
        if share in NONORDINARY or isinstance(sector,str) and sector.startswith("SPAC"):
            return "excluded"
        return "ordinary" if share == "보통주" and sector in SECTORS else "unknown"
    except (KeyError,TypeError,ValueError,AttributeError) as exc:
        raise DataQualityError("invalid_security_classification") from exc


def security_inputs(db, ref_date: date, now: datetime) -> dict[str, dict]:
    """Freeze latest available metadata for each exact-date ticker."""
    rows = db.query(FujimotoEvidence).filter_by(kind="security_source").filter(
        FujimotoEvidence.observed_at <= utc_naive(now), FujimotoEvidence.available_at <= utc_naive(now)).order_by(FujimotoEvidence.id)
    return {r.ticker:r.payload for r in rows if r.payload.get("ref_date") == ref_date.isoformat()}


def collect_metadata(db, ref_date: date) -> dict:
    """One bounded all-market KRX request; errors remain visible and fail closed."""
    from maps.data.krx_adapter import fetch_basic_information
    from maps.common.exceptions import DataCollectionError
    from requests import RequestException
    if ref_date != datetime.now(timezone.utc).astimezone(KST).date():
        raise DataQualityError("current_official_metadata_required")
    try:
        frame = fetch_basic_information()
    except (DataCollectionError, ValueError, OSError, RequestException) as exc:
        return {"status":"partial","reason":str(exc),"collected":0}
    counts = Counter()
    for raw in frame.to_dict("records"):
        # KRX sends empty strings; don't turn missing/nonfinite fields into metadata.
        raw = {k:v for k,v in raw.items() if isinstance(v,(str,int,float,bool)) and not (isinstance(v,float) and not isfinite(v))}
        if raw.get("MKT_TP_NM") not in {"KOSPI","KOSDAQ","KOSDAQ GLOBAL"}:
            continue
        row = record_security_source(db,ref_date,raw)
        counts[security_kind(row.payload,row.ticker,ref_date)] += 1
    now = datetime.now(timezone.utc)
    universes = [r for r in FujimotoRepository(db).evidence_as_of("universe","*",now)
                 if r.payload.get("ref_date") == ref_date.isoformat()]
    expected = {m["ticker"] for m in universes[-1].payload["members"]} if universes else set()
    observed = security_inputs(db,ref_date,now)
    missing = sorted(expected-observed.keys())
    return {"status":"partial" if missing or counts.get("unknown",0) else "success",
            "collected":sum(counts.values()),"states":dict(counts),"missing_tickers":missing,
            "expected":len(expected),"universe_observed":bool(universes)}


def valuation_report(db, ref_date: date, *, now: datetime | None = None, tickers: list[str] | None = None) -> dict:
    """Read-only coverage/reason counts and same-data frozen-candidate v2/v3 comparison."""
    import base64
    import json
    import zlib
    from sqlalchemy import func
    from maps.common.models import SecurityFundamental
    from maps.fujimoto.sources import valuation_inputs
    from maps.fujimoto.replay import screening_evidence
    now = now or datetime.now(timezone.utc)
    classifications = security_inputs(db,ref_date,now)
    exclusions = exclusion_inputs(db,ref_date,now)
    raw_vals = [dict(ticker=r.ticker,ref_date=r.date,available_at=r.updated_at,per=r.per,provider=r.source)
        for r in db.query(SecurityFundamental).filter(SecurityFundamental.date == ref_date,
            SecurityFundamental.updated_at <= utc_naive(now))]
    values = {r["ticker"]:r for r in valuation_inputs(db,ref_date,now,raw_vals)}
    universes = [r for r in FujimotoRepository(db).evidence_as_of("universe","*",now)
                 if r.payload.get("ref_date") == ref_date.isoformat()]
    members = {m["ticker"] for m in universes[-1].payload["members"]} if universes else set(values)|set(classifications)
    selected = members if tickers is None else set(tickers)
    sources = {}
    for r in db.query(FujimotoEvidence).filter_by(kind="eps_source").filter(
            FujimotoEvidence.observed_at <= utc_naive(now)).order_by(FujimotoEvidence.id):
        if r.payload.get("year") == applicable_year(ref_date):
            sources[r.ticker] = r
    states, reasons, rows = Counter(), Counter(), []
    for ticker in sorted(selected):
        kind = security_kind(classifications.get(ticker),ticker,ref_date)
        value = values.get(ticker,{})
        per = value.get("per")
        reason = None
        if kind == "unknown":
            state,reason = "unknown","missing_security_classification"
        elif kind == "excluded":
            state = "excluded_security"
        elif per is not None and per > 0:
            state = "numeric"
        elif ticker in exclusions:
            state = "negative_common_eps"
        elif value.get("status") == "verified_not_applicable":
            state = "verified_not_applicable"
        else:
            state,reason = "unknown","missing_eps_source"
            source = sources.get(ticker)
            if source:
                try:
                    eps = _source_eps(source.payload)
                    reason = "nonnegative_common_eps" if eps >= 0 else "pending_causal_review_or_revision"
                except DataQualityError as exc:
                    reason = str(exc)
        states[state] += 1
        if reason:
            reasons[reason] += 1
        rows.append(dict(ticker=ticker,state=state,reason=reason,per=per,
                         source_id=sources[ticker].id if ticker in sources else None))
    candidates = ()
    if selected:
        # Select IDs before fetching JSON: historical/raw_zlib payloads can be large.
        # Preserve the latest appended eligible revision, not the latest observation.
        latest = db.query(func.max(FujimotoEvidence.id)).filter(
            FujimotoEvidence.kind == "candidate",
            FujimotoEvidence.ticker.in_(selected),
            FujimotoEvidence.payload["ref_date"].as_string() == ref_date.isoformat(),
            FujimotoEvidence.observed_at <= utc_naive(now),
            FujimotoEvidence.available_at <= utc_naive(now)).group_by(FujimotoEvidence.ticker)
        candidates = db.query(FujimotoEvidence).filter(FujimotoEvidence.id.in_(latest)).order_by(
            FujimotoEvidence.ticker).yield_per(1)
    comparison = {"v2_selected":0,"v3_selected":0,"reasons_v2":Counter(),"reasons_v3":Counter()}
    comparison_candidates = 0
    for candidate in candidates:
        ticker = candidate.ticker
        comparison_candidates += 1
        raw = json.loads(zlib.decompress(base64.b64decode(candidate.payload["raw_zlib"])))
        # Compare exactly the same frozen inputs. No prospective backdating or DB enrichment.
        for version in (2,3):
            try:
                rule = screening_evidence({**raw,"screening_version":version},ticker,ref_date)
                comparison[f"v{version}_selected"] += rule.selection_passed
                comparison[f"reasons_v{version}"].update(set(rule.blocking_reasons))
            except DataQualityError as exc:
                comparison[f"reasons_v{version}"][str(exc)] += 1
    return json_data(dict(ref_date=ref_date,observed_at=now,states=dict(states),reasons=dict(reasons),
        rows=rows,comparison=comparison,comparison_candidates=comparison_candidates,
        metadata_coverage=dict(expected=len(members),observed=len(members & classifications.keys()),
            missing=sorted(members-classifications.keys())),execution_permission=False))


class EPSCollector:
    """Bounded all-peer EPS ingestion; never calls the growth/eligible-only parser."""
    def __init__(self, db, api_key: str, *, client=None):
        self.db, self.api_key, self.client = db, api_key, client

    def collect(self, ref_date: date, *, batch: int = 20, tickers: list[str] | None = None) -> dict:
        """Discover latest annual corrections before requesting exact EPS statement rows."""
        if ref_date != datetime.now(timezone.utc).astimezone(KST).date() or not 1 <= batch <= 500:
            raise DataQualityError("current_bounded_eps_collection_required")
        now = datetime.now(timezone.utc)
        repo = FujimotoRepository(self.db)
        universes = repo.evidence_as_of("universe","*",now)
        latest = max(universes,key=lambda r:(r.payload.get("ref_date",""),r.id)) if universes else None
        allowed = {m["ticker"] for m in latest.payload["members"]} if latest else set()
        classifications = security_inputs(self.db,ref_date,now)
        allowed = {t for t in allowed if security_kind(classifications.get(t),t,ref_date) != "excluded"}
        targets = sorted(allowed if tickers is None else allowed & set(tickers))
        checked = {r.ticker:r.id for r in self.db.query(FujimotoEvidence).filter_by(kind="eps_collection").order_by(FujimotoEvidence.id)}
        # Positive KRX inputs need no EPS diagnostic; ineligible negative peers do.
        from maps.common.models import SecurityFundamental
        positive = {r.ticker for r in self.db.query(SecurityFundamental).filter(
            SecurityFundamental.date == ref_date,SecurityFundamental.per > 0,
            SecurityFundamental.source.in_(("pykrx","krx")))}
        targets = sorted((t for t in targets if t not in positive),key=lambda t:(checked.get(t,0),t))[:batch]
        summary = dict(status="success",attempted=[],sources=[],errors={},requests=0,
                       rejected_targets=sorted(set(tickers or [])-allowed))
        if not self.api_key:
            return {**summary,"status":"insufficient","reason":"missing_dart_api_key"}
        client = self.client or DartClient(self.api_key)
        try:
            mapping = client.corp_codes() if targets else {}
            if targets:
                known_tickers = {r.ticker for r in self.db.query(FujimotoEvidence).filter_by(kind="eps_source")}
                for ticker in sorted(known_tickers | set(targets)):
                    _record_identity_observation(self.db,ticker,{ticker:mapping.get(ticker)})
            for ticker in targets:
                summary["attempted"].append(ticker)
                error = None
                try:
                    corp = mapping.get(ticker)
                    if not corp:
                        raise DataQualityError("corp_code_missing")
                    year = applicable_year(ref_date)
                    filings, page = [], 1
                    while True:
                        response = client.request("list.json",corp_code=corp,bgn_de=f"{year+1}0101",
                            end_de=ref_date.strftime("%Y%m%d"),pblntf_ty="A",last_reprt_at="N",page_no=page,
                            page_count=100,sort="date",sort_mth="desc")
                        for r in response.get("list",[]):
                            if re.search(rf"사업보고서\s*\({year}\.12\)",r.get("report_nm","")):
                                if r.get("corp_code",corp) != corp or r.get("stock_code",ticker) != ticker:
                                    raise DataQualityError("filing_identity_mismatch")
                                publication = datetime.strptime(r["rcept_dt"],"%Y%m%d").date()
                                filing = DartFinancialRepository(self.db).record_receipt(ticker,r["rcept_no"],date(year,12,31),publication,datetime.now(timezone.utc))
                                filings.append((filing,r))
                        self.db.commit()
                        if page >= int(response.get("total_page",1)):
                            break
                        page += 1
                    if not filings:
                        raise DataQualityError("applicable_annual_filing_missing")
                    filing, filing_raw = max(filings,key=lambda f:(f[0].publication_date,f[0].receipt))
                    response = client.request("fnlttSinglAcntAll.json",corp_code=corp,bsns_year=str(year),reprt_code="11011",fs_div="CFS")
                    basis, cfs = "CFS", None
                    if response.get("status") == "013":
                        basis,cfs = "OFS",response
                        response = client.request("fnlttSinglAcntAll.json",corp_code=corp,bsns_year=str(year),reprt_code="11011",fs_div="OFS")
                    source = record_eps_source(self.db,ticker,corp_code=corp,receipt=filing.receipt,year=year,basis=basis,
                        publication_date=filing.publication_date,response=response,cfs_nonapplicability=cfs,
                        identity={ticker:corp},filing=filing_raw)
                    summary["sources"].append(source.id)
                    try:
                        _source_eps(source.payload)
                    except DataQualityError as exc:
                        error = str(exc)
                except (ValueError,DataQualityError,DartStop) as exc:
                    error = str(exc)
                    if isinstance(exc,DartStop):
                        summary["status"] = "partial"
                stamp = datetime.now(timezone.utc)
                repo.record_evidence("eps_collection",ticker,stamp,stamp,{"error":error})
                self.db.commit()
                if error:
                    summary["errors"][ticker] = error
                if summary["status"] == "partial":
                    break
        except (ValueError,DartStop) as exc:
            summary.update(status="partial",reason=str(exc))
        summary["requests"] = client.requests
        if summary["errors"]:
            summary["status"] = "partial"
        return summary
