"""Bounded offline price recovery; immutable source evidence and atomic CAS writes.

Prepared envelopes are a trusted operator boundary, not authenticated KRX fetches.
No network, strategy evaluation, historical availability rewrite or broker calls.
"""
from __future__ import annotations

import base64
import binascii
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import math
import re

from sqlalchemy import and_, delete, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from maps.common.exceptions import DataQualityError
from maps.common.models import CollectionLog, FujimotoEvidence, HistoricalOHLCV
from maps.fujimoto.repository import FujimotoRepository, fingerprint as evidence_fingerprint
from maps.market.trading_rules import is_krx_closed_date

SOURCE_URL = "https://data.krx.co.kr/contents/MDC/STAT/standard/MDCSTAT017.jsp"
SOURCE_BLD = "dbms/MDC/STAT/standard/MDCSTAT01701"
MAX_ROWS = 100_000
MAX_CELLS = 2_000_000
MAX_RAW_BYTES = 10_000_000
KST = timezone(timedelta(hours=9))
PRICES = ("open", "high", "low", "close", "volume", "adj_close")
SNAPSHOT_FIELDS = (*PRICES, "source", "created_at", "updated_at")
RAW_FIELDS = {"open": "TDD_OPNPRC", "high": "TDD_HGPRC", "low": "TDD_LWPRC",
              "close": "TDD_CLSPRC", "volume": "ACC_TRDVOL"}


def _utc_now() -> datetime:
    """Return aware actual observation time, never a repaired price date."""
    return datetime.now(timezone.utc)


def _timestamp(value: str) -> datetime:
    """Require explicit UTC/offset times at external boundaries."""
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise DataQualityError("timestamp_requires_offset")
    return result.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    """Serialize database UTC-naive timestamps without local-time interpretation."""
    return value.replace(tzinfo=timezone.utc).isoformat() if value.tzinfo is None else value.astimezone(timezone.utc).isoformat()


def _json(value: object) -> bytes:
    """Canonical strict JSON for independently reproducible hashes."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def manifest_digest(manifest: dict) -> str:
    """Compute a content checksum, not a signature or source authentication."""
    return hashlib.sha256(_json({k: v for k, v in manifest.items() if k != "manifest_id"})).hexdigest()


def _scope(start: date, end: date, tickers: list[str]) -> list[str]:
    """Bound all queries and emitted missing-input cells by explicit identities."""
    if start > end or (end - start).days > 3650 or not tickers or len(tickers) > 5000:
        raise DataQualityError("invalid_recovery_scope")
    if len(tickers) != len(set(tickers)) or any(not isinstance(t, str) or not re.fullmatch(r"[0-9A-Z]{6}", t) for t in tickers):
        raise DataQualityError("invalid_recovery_scope_tickers")
    if len(tickers) * ((end - start).days + 1) > MAX_CELLS:
        raise DataQualityError("recovery_scope_too_large")
    return sorted(tickers)


def _clean_session(db: Session) -> None:
    """Never flush or commit another caller's pending work, including dry runs."""
    if db.new or db.dirty or db.deleted:
        raise DataQualityError("recovery_requires_clean_session")


def _snapshot(row: HistoricalOHLCV | None) -> dict | None:
    """Freeze every stored data/provenance/time field; retain nonfinite originals."""
    if row is None:
        return None
    result = {}
    for field in SNAPSHOT_FIELDS:
        value = getattr(row, field)
        if isinstance(value, datetime):
            value = _iso(value)
        elif isinstance(value, float) and not math.isfinite(value):
            value = str(value)
        result[field] = value
    return result


def _valid_bar(row: dict) -> bool:
    """Check finite positive prices, integral bounded volume and OHLC range."""
    try:
        for field in PRICES:
            value = row[field]
            if field == "adj_close" and value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                return False
            if value < 0 or (field != "volume" and value == 0):
                return False
        volume = row["volume"]
        return (volume == int(volume) and volume <= 2_147_483_647
                and row["low"] <= row["open"] <= row["high"]
                and row["low"] <= row["close"] <= row["high"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


def _number(raw: object, *, volume: bool = False) -> float | int:
    """Parse source strings strictly; do not silently coerce a fractional volume."""
    if not isinstance(raw, str) or not re.fullmatch(r"(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?", raw):
        raise DataQualityError("malformed_source_number")
    normalized = raw.replace(",", "")
    if volume:
        exact = Decimal(normalized)
        if exact != exact.to_integral_value() or exact > 2_147_483_647:
            raise DataQualityError("invalid_source_number")
        return int(exact)
    number = float(normalized)
    if not math.isfinite(number):
        raise DataQualityError("invalid_source_number")
    return number


def parse_envelope(envelope: dict) -> list[dict]:
    """Verify one unadjusted per-security request, exact response hash and rows.

    ISIN-to-ticker binding verifies envelope consistency. The caller must separately
    establish the prepared response really came from the stated official source.
    """
    try:
        if envelope["version"] != 1 or envelope["source_url"] != SOURCE_URL:
            raise DataQualityError("untrusted_source")
        ticker, request = envelope["ticker"], envelope["request"]
        if (not re.fullmatch(r"[0-9A-Z]{6}", ticker)
                or not re.fullmatch(r"KR7" + ticker + r"[0-9A-Z]{3}", request["isuCd"])
                or request["bld"] != SOURCE_BLD or str(request["adjStkPrc"]) != "1"):
            raise DataQualityError("source_identity_or_price_basis_mismatch")
        observed = _timestamp(envelope["observed_at"])
        if observed > _utc_now():
            raise DataQualityError("source_observed_in_future")
        start = datetime.strptime(request["strtDd"], "%Y%m%d").date()
        end = datetime.strptime(request["endDd"], "%Y%m%d").date()
        if start > end:
            raise DataQualityError("source_date_range")
        if len(envelope["raw_base64"]) > MAX_RAW_BYTES * 4 // 3 + 4:
            raise DataQualityError("source_too_large")
        raw = base64.b64decode(envelope["raw_base64"], validate=True)
        if len(raw) > MAX_RAW_BYTES or hashlib.sha256(raw).hexdigest() != envelope["raw_sha256"]:
            raise DataQualityError("source_hash_mismatch")
        body = json.loads(raw)
        keys = [key for key in ("output", "OutBlock_1") if key in body]
        if len(keys) != 1 or not isinstance(body[keys[0]], list) or len(body[keys[0]]) > MAX_ROWS:
            raise DataQualityError("source_rows_shape")
        rows, seen = [], set()
        for index, raw_row in enumerate(body[keys[0]]):
            day = datetime.strptime(raw_row["TRD_DD"], "%Y/%m/%d").date()
            if not start <= day <= end or day in seen:
                raise DataQualityError("source_duplicate_or_out_of_range")
            # Total trading volume can change after the regular session closes.
            if day >= observed.astimezone(KST).date():
                raise DataQualityError("source_incomplete_session")
            seen.add(day)
            values = {field: _number(raw_row[key], volume=field == "volume") for field, key in RAW_FIELDS.items()}
            values.update(adj_close=None, source="krx_per_security")
            # KRX may carry the prior close through a no-trade/suspension row.
            no_trade = all(values[field] == 0 for field in ("open", "high", "low", "volume"))
            if not no_trade and not _valid_bar(values):
                raise DataQualityError("source_invalid_ohlcv")
            rows.append({"ticker": ticker, "date": day.isoformat(), "values": values,
                         "raw_row": index, "observed_at": observed.isoformat(), "no_trade": no_trade})
        return rows
    except (KeyError, TypeError, ValueError, binascii.Error, UnicodeError) as exc:
        raise DataQualityError("malformed_source_envelope") from exc


def inventory(db: Session, *, start: date, end: date, tickers: list[str],
              extra_closed_dates: tuple[date, ...] = ()) -> dict:
    """Read bounded exact input cells and failures; unknown halts stay unknown."""
    _clean_session(db)
    tickers = _scope(start, end, tickers)
    stored = {(r.ticker, r.date): _snapshot(r) for r in db.query(HistoricalOHLCV).filter(
        HistoricalOHLCV.ticker.in_(tickers), HistoricalOHLCV.date.between(start, end)).all()}
    counts = {key: 0 for key in ("present", "invalid", "missing_unclassified", "nontrading")}
    gaps, invalid = [], []
    for offset in range((end - start).days + 1):
        day = start + timedelta(days=offset)
        closed = is_krx_closed_date(day, extra_closed_dates=extra_closed_dates)
        for ticker in tickers:
            row = stored.get((ticker, day))
            if row is not None:
                status = "present" if _valid_bar(row) else "invalid"
                if status == "invalid":
                    invalid.append({"ticker": ticker, "date": day.isoformat(), "before": row})
            elif closed:
                status = "nontrading"
            else:
                status = "missing_unclassified"
                gaps.append({"ticker": ticker, "date": day.isoformat(), "reason": "missing_unclassified"})
            counts[status] += 1
    logs = db.query(CollectionLog).filter(CollectionLog.ref_date.between(start, end),
                                         CollectionLog.status.in_(("partial", "failed"))).order_by(CollectionLog.id).limit(MAX_ROWS + 1).all()
    if len(logs) > MAX_ROWS:
        raise DataQualityError("inventory_failure_limit")
    return {"version": 1, "observed_at": _utc_now().isoformat(), "start": start.isoformat(),
            "end": end.isoformat(), "tickers": tickers, "counts": counts, "gaps": gaps, "invalid": invalid,
            "collection_failures": [{"id": r.id, "date": r.ref_date.isoformat(), "status": r.status,
                                     "source": r.source, "note": r.note} for r in logs],
            "limitations": ["Missing sessions are not proof of a collection error or halt.",
                            "No durable historical halt source; unknown gaps remain unclassified."]}


def build_manifest(db: Session, sources: list[dict], *, start: date, end: date,
                   tickers: list[str], extra_closed_dates: tuple[date, ...] = ()) -> dict:
    """Produce exact planned changes without flushing, committing or collecting."""
    _clean_session(db)
    tickers = _scope(start, end, tickers)
    if not isinstance(sources, list) or len(sources) > 5000:
        raise DataQualityError("invalid_source_list")
    generated = _utc_now().isoformat()
    manifest = {"version": 1, "generated_at": generated, "start": start.isoformat(), "end": end.isoformat(),
                "tickers": tickers, "extra_closed_dates": [d.isoformat() for d in extra_closed_dates],
                "sources": sources, "changes": [], "unrepairable": [],
                "source_basis": {"ohlc": "KRX regular-session unadjusted", "volume": "KRX total trading volume"},
                "limitations": ["Trusted prepared envelopes only; no authenticated source fetch integration.",
                                "Missing source dates are unknown, not proof of a halt or repairable bar.",
                                "Source observation and audit availability are actual collection/application times."]}
    seen = set()
    for source_index, source in enumerate(sources):
        for raw in parse_envelope(source):
            ticker, day = raw["ticker"], date.fromisoformat(raw["date"])
            if ticker not in tickers or not start <= day <= end:
                raise DataQualityError("source_outside_explicit_scope")
            identity = (ticker, day)
            if identity in seen or len(seen) >= MAX_ROWS:
                raise DataQualityError("duplicate_or_excess_source_rows")
            seen.add(identity)
            if raw["no_trade"] or is_krx_closed_date(day, extra_closed_dates=extra_closed_dates):
                manifest["unrepairable"].append({"ticker": ticker, "date": day.isoformat(),
                                                "reason": "source_no_trade" if raw["no_trade"] else "nontrading"})
                continue
            before = _snapshot(db.query(HistoricalOHLCV).filter_by(ticker=ticker, date=day).one_or_none())
            after = {**raw["values"], "created_at": before["created_at"] if before else generated, "updated_at": generated}
            if before and all(before[key] == after[key] for key in (*PRICES, "source")):
                continue
            manifest["changes"].append({"ticker": ticker, "date": day.isoformat(), "before": before,
                                        "after": after, "source_index": source_index, "raw_row": raw["raw_row"],
                                        "observed_at": raw["observed_at"]})
    manifest["manifest_id"] = manifest_digest(manifest)
    return manifest


def _validate_manifest(manifest: dict) -> None:
    """Reparse raw sources and reject changed prices even with a recomputed hash."""
    try:
        if manifest["version"] != 1 or manifest["manifest_id"] != manifest_digest(manifest):
            raise DataQualityError("manifest_hash_mismatch")
        start, end = date.fromisoformat(manifest["start"]), date.fromisoformat(manifest["end"])
        tickers = _scope(start, end, manifest["tickers"])
        generated = _timestamp(manifest["generated_at"])
        if generated > _utc_now() or len(manifest["changes"]) > MAX_ROWS or len(manifest["sources"]) > 5000:
            raise DataQualityError("invalid_manifest_bounds")
        parsed = [parse_envelope(source) for source in manifest["sources"]]
        closed_dates = tuple(date.fromisoformat(s) for s in manifest["extra_closed_dates"])
        seen = set()
        for change in manifest["changes"]:
            ticker, day = change["ticker"], date.fromisoformat(change["date"])
            identity = (ticker, day)
            if ticker not in tickers or not start <= day <= end or identity in seen:
                raise DataQualityError("manifest_scope_or_duplicate")
            if is_krx_closed_date(day, extra_closed_dates=closed_dates):
                raise DataQualityError("manifest_nontrading_source")
            seen.add(identity)
            raw = parsed[change["source_index"]][change["raw_row"]]
            if raw["ticker"] != ticker or raw["date"] != change["date"] or raw["no_trade"] or raw["observed_at"] != change["observed_at"]:
                raise DataQualityError("manifest_source_identity_mismatch")
            if any(change["after"][key] != value for key, value in raw["values"].items()):
                raise DataQualityError("manifest_source_price_mismatch")
            if not _valid_bar(change["after"]) or set(change["after"]) != set(SNAPSHOT_FIELDS):
                raise DataQualityError("manifest_invalid_after")
            expected_created = change["before"]["created_at"] if change["before"] else manifest["generated_at"]
            if (change["after"]["updated_at"] != manifest["generated_at"]
                    or change["after"]["created_at"] != expected_created
                    or _timestamp(change["observed_at"]) > generated):
                raise DataQualityError("manifest_timestamp_mismatch")
            for snapshot in (change["before"], change["after"]):
                if snapshot is not None:
                    if set(snapshot) != set(SNAPSHOT_FIELDS):
                        raise DataQualityError("manifest_snapshot_shape")
                    _timestamp(snapshot["created_at"])
                    _timestamp(snapshot["updated_at"])
    except (KeyError, TypeError, ValueError, IndexError, OverflowError) as exc:
        raise DataQualityError("malformed_manifest") from exc


def _values(snapshot: dict) -> dict:
    """Restore persisted UTC times and any original nonfinite legacy numbers."""
    values = dict(snapshot)
    for field in ("created_at", "updated_at"):
        values[field] = _timestamp(values[field]).replace(tzinfo=None)
    for field in PRICES:
        if values[field] in ("inf", "-inf", "nan"):
            values[field] = float(values[field])
    return values


def _condition(ticker: str, day: date, snapshot: dict):
    """Match all frozen fields inside SQL, closing read-check/write races."""
    return and_(HistoricalOHLCV.ticker == ticker, HistoricalOHLCV.date == day,
                *(getattr(HistoricalOHLCV, k) == v for k, v in _values(snapshot).items()))


def _mutate(db: Session, manifest: dict, *, rollback: bool) -> dict:
    """Apply/reverse a reviewed whole manifest and append a receipt atomically."""
    _validate_manifest(manifest)
    _clean_session(db)
    manifest_id = manifest["manifest_id"]
    operation = "rollback" if rollback else "apply"
    payloads = {op: {"operation": op, "manifest": manifest, "changed": len(manifest["changes"])}
                for op in ("apply", "rollback")}
    try:
        receipts = db.query(FujimotoEvidence).filter_by(kind="operational_recovery", ticker="*").filter(
            FujimotoEvidence.fingerprint.in_([evidence_fingerprint(payload) for payload in payloads.values()])
        ).with_for_update().all()
        operations = {r.payload["operation"] for r in receipts}
        if rollback and "apply" not in operations:
            raise DataQualityError("manifest_not_applied")
        if not rollback and "rollback" in operations:
            raise DataQualityError("manifest_already_rolled_back")
        already_done = operation in operations
        for change in manifest["changes"]:
            ticker, day = change["ticker"], date.fromisoformat(change["date"])
            expected, desired = (change["after"], change["before"]) if rollback else (change["before"], change["after"])
            actual = _snapshot(db.query(HistoricalOHLCV).filter_by(ticker=ticker, date=day).populate_existing().one_or_none())
            if actual != (desired if already_done else expected):
                raise DataQualityError(f"recovery_conflict:{ticker}:{day}")
        if already_done or not manifest["changes"]:
            db.rollback()
            return {"operation": operation, "manifest_id": manifest_id, "changed": 0}
        for change in manifest["changes"]:
            ticker, day = change["ticker"], date.fromisoformat(change["date"])
            expected, desired = (change["after"], change["before"]) if rollback else (change["before"], change["after"])
            if expected is None:
                db.add(HistoricalOHLCV(ticker=ticker, date=day, **_values(desired)))
                db.flush()
            else:
                condition = _condition(ticker, day, expected)
                statement = delete(HistoricalOHLCV).where(condition) if desired is None else update(HistoricalOHLCV).where(condition).values(**_values(desired))
                if db.execute(statement, execution_options={"synchronize_session": False}).rowcount != 1:
                    raise DataQualityError(f"recovery_conflict:{ticker}:{day}")
        now = _utc_now().replace(tzinfo=None)
        FujimotoRepository(db).record_evidence("operational_recovery", "*", now, now, payloads[operation])
        db.commit()
        return {"operation": operation, "manifest_id": manifest_id, "changed": len(manifest["changes"])}
    except IntegrityError as exc:
        db.rollback()
        raise DataQualityError("recovery_conflict:concurrent_insert") from exc
    except Exception:
        db.rollback()
        raise


def apply_manifest(db: Session, manifest: dict) -> dict:
    """Apply explicit reviewed source evidence; repeat calls make zero changes."""
    return _mutate(db, manifest, rollback=False)


def rollback_manifest(db: Session, manifest: dict) -> dict:
    """Reverse only this audited manifest if all touched rows still match."""
    return _mutate(db, manifest, rollback=True)
