"""Durable bounded OHLCV runs; no network call holds a database transaction."""
from __future__ import annotations
import datetime as dt
from dataclasses import asdict
import uuid
from zoneinfo import ZoneInfo
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from maps.common.exceptions import DataCollectionError
from maps.common.models import OhlcvCollectionRun, OhlcvCollectionItem, HistoricalOHLCV
from maps.data.krx_adapter import CollectionResult, OHLCVData, SecurityMeta


def _now():
    """UTC-naive timestamp for the existing database convention."""
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def _json(value):
    """Serialize only supported snapshot primitives."""
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json(v) for v in value]
    return value


def save_collection(db, result: CollectionResult) -> None:
    """Attach auxiliary daily inputs after the complete collection returns."""
    run_id = (result.ohlcv_quality or {}).get("run_id")
    if run_id is None:
        return
    run = db.get(OhlcvCollectionRun, run_id)
    if run is None or run.published_at is None:
        raise DataCollectionError("normalized_collection_not_published")
    snapshot = _json(asdict(result))
    changed = db.execute(update(OhlcvCollectionRun).where(
        OhlcvCollectionRun.id == run_id, OhlcvCollectionRun.snapshot.is_(None),
        OhlcvCollectionRun.published_at.is_not(None),
    ).values(snapshot=snapshot)).rowcount
    db.commit()
    if not changed:
        db.refresh(run)
        if run.snapshot != snapshot:
            raise DataCollectionError("normalized_collection_snapshot_immutable")


def load_collection(db, ref_date: dt.date) -> CollectionResult | None:
    """Restore only fully recorded input, never partially written prices."""
    run = db.query(OhlcvCollectionRun).filter_by(ref_date=ref_date).first()
    if (run is None or run.snapshot is None or run.published_at is None
            or run.status not in ("success", "partial") or not run.quality.get("market_complete")
            or not run.quality.get("valid_count")):
        return None
    data = dict(run.snapshot)
    data["ref_date"] = dt.date.fromisoformat(data["ref_date"])
    data["ohlcv"] = [OHLCVData(**{**r, "date": dt.date.fromisoformat(r["date"])}) for r in data["ohlcv"]]
    data["meta"] = [SecurityMeta(**{**r, **{k: dt.date.fromisoformat(r[k]) if r.get(k) else None
                       for k in ("listing_date", "delisting_date")}}) for r in data["meta"]]
    return CollectionResult(**data)


class NormalizedDailyCollector:
    """Checkpoint source observations and atomically publish their validated prices."""
    def __init__(self, db, source=None):
        self.db = db
        if source is None:
            from maps.data.ohlcv_source import NormalizedKRXSource
            source = NormalizedKRXSource()
        self.source = source

    def collect(self, ref_date: dt.date):
        """Return verified prices and additive quality; an active owner blocks duplicates."""
        from maps.data.collector import DataCollector
        now = _now()
        if ref_date != now.replace(tzinfo=dt.timezone.utc).astimezone(ZoneInfo("Asia/Seoul")).date():
            raise DataCollectionError("normalized_collection_requires_current_session")
        run = self.db.query(OhlcvCollectionRun).filter_by(ref_date=ref_date).first()
        if run is None:
            run = OhlcvCollectionRun(ref_date=ref_date, status="pending", started_at=now,
                                     expected={}, requests=0, quality={})
            self.db.add(run)
            try:
                self.db.commit()
            except IntegrityError:
                self.db.rollback()
                run = self.db.query(OhlcvCollectionRun).filter_by(ref_date=ref_date).one()
        run_id = run.id
        if run.published_at is not None:
            return self._published(run)
        if run.status == "failed":
            raise DataCollectionError("normalized_collection_failed")
        owner = uuid.uuid4().hex
        claimed = self.db.execute(update(OhlcvCollectionRun).where(
            OhlcvCollectionRun.id == run_id,
            ((OhlcvCollectionRun.owner.is_(None)) | (OhlcvCollectionRun.lease_until < now)),
            OhlcvCollectionRun.published_at.is_(None),
        ).values(owner=owner, lease_until=now + dt.timedelta(seconds=60), status="running")).rowcount
        self.db.commit()
        if not claimed:
            raise DataCollectionError("normalized_collection_in_progress")
        self.db.refresh(run)
        expected, requests, started = dict(run.expected), run.requests, run.started_at
        stored = self.db.query(OhlcvCollectionItem.ticker).filter_by(run_id=run_id).all()
        skipped = [r[0] for r in stored]
        self.db.commit()

        def heartbeat(*, commit=True, **values):
            count = self.db.execute(update(OhlcvCollectionRun).where(
                OhlcvCollectionRun.id == run_id, OhlcvCollectionRun.owner == owner,
                OhlcvCollectionRun.status == "running",
            ).values(lease_until=_now() + dt.timedelta(seconds=60), **values)).rowcount
            if not count:
                self.db.rollback()
                raise DataCollectionError("normalized_collection_lease_lost")
            if commit:
                self.db.commit()

        def on_universe(identities):
            nonlocal expected
            if expected and dict(identities) != expected:
                raise DataCollectionError("normalized_collection_identity_changed")
            expected = dict(identities)
            heartbeat(expected=expected)

        def on_item(item):
            if (item.ticker not in expected or item.date != ref_date or
                    (item.bar is not None and (item.bar.ticker != item.ticker or item.bar.date != ref_date))):
                raise DataCollectionError("normalized_collection_item_identity_mismatch")
            if item.status not in ("valid", "adjustment_missing", "no_trade", "failed", "unprocessed"):
                raise DataCollectionError("normalized_collection_item_status_invalid")
            heartbeat(commit=False)
            self.db.add(OhlcvCollectionItem(run_id=run_id, ticker=item.ticker,
                status=item.status, payload=item.as_dict(), observed_at=_now()))
            self.db.commit()

        try:
            from maps.common.settings import get_settings
            max_seconds = getattr(self.source, "max_seconds", get_settings().maps_ohlcv_max_seconds)
            batch = self.source.collect(ref_date, expected=expected or None,
                skip_tickers=skipped, initial_requests=requests,
                remaining_seconds=max(0, max_seconds - (_now() - started).total_seconds()),
                on_universe=on_universe,
                on_universe_evidence=lambda evidence: heartbeat(quality={"universe_evidence": evidence}),
                on_request=lambda count: heartbeat(requests=count), on_item=on_item)
            heartbeat()
            self.db.refresh(run)
            items = self.db.query(OhlcvCollectionItem).filter_by(run_id=run_id).all()
            if {item.ticker for item in items} != set(run.expected):
                raise DataCollectionError("normalized_collection_items_incomplete")
            quality = {"run_id": run_id, "received_count": len(run.expected), "saved_count": 0,
                "rejected_count": 0, "rejected_rows": [], "no_trade_count": 0,
                "adjustment_missing_count": 0, "failed_count": 0, "unprocessed_count": 0,
                "source": "krx_per_security", "price_basis": "regular_unadjusted",
                "volume_basis": "observed", "requests": run.requests,
                "market_complete": batch.market_complete, "preserved_count": 0,
                "universe_evidence": (run.quality or {}).get("universe_evidence", [])}
            bars = []
            for item in items:
                payload = item.payload
                if payload.get("ticker") != item.ticker or payload.get("date") != ref_date.isoformat():
                    raise DataCollectionError("normalized_collection_item_identity_mismatch")
                if item.status == "no_trade":
                    quality["no_trade_count"] += 1
                    continue
                if item.status in ("valid", "adjustment_missing") and payload.get("bar"):
                    raw = dict(payload["bar"])
                    raw["date"] = dt.date.fromisoformat(raw["date"])
                    bar = OHLCVData(**raw)
                    if bar.date != ref_date or bar.ticker != item.ticker:
                        raise DataCollectionError("normalized_collection_item_identity_mismatch")
                    reason = DataCollector._ohlcv_rejection_reason(bar)
                    if reason is None:
                        bars.append(bar)
                        quality["adjustment_missing_count"] += item.status == "adjustment_missing"
                        continue
                quality["rejected_count"] += 1
                quality["unprocessed_count" if item.status == "unprocessed" else "failed_count"] += 1
                quality["rejected_rows"].append({"ticker": item.ticker, "date": ref_date.isoformat(),
                    "reason": payload.get("reason") or "invalid_normalized_bar"})
            if not batch.market_complete or not bars:
                raise DataCollectionError(batch.error or "normalized_source_unavailable")
            if _now().replace(tzinfo=dt.timezone.utc).astimezone(ZoneInfo("Asia/Seoul")).date() != ref_date:
                raise DataCollectionError("normalized_collection_session_changed")
            # The owner fence and all price writes share this transaction.
            fence = self.db.execute(update(OhlcvCollectionRun).where(
                OhlcvCollectionRun.id == run_id, OhlcvCollectionRun.owner == owner,
                OhlcvCollectionRun.status == "running").values(lease_until=_now() + dt.timedelta(seconds=60)))
            if fence.rowcount != 1:
                raise DataCollectionError("normalized_collection_lease_lost")
            for bar in bars:
                row = self.db.query(HistoricalOHLCV).filter_by(date=ref_date,ticker=bar.ticker).first()
                if (row is not None and bar.adj_close is None and row.adj_close is not None
                        and DataCollector._ohlcv_rejection_reason(row) is None):
                    quality["preserved_count"] += 1
                    continue
                if row is None:
                    row = HistoricalOHLCV(date=ref_date, ticker=bar.ticker)
                    self.db.add(row)
                for field in ("open", "high", "low", "close", "volume", "adj_close"):
                    setattr(row, field, getattr(bar, field))
                row.source = "krx_per_security"
                row.updated_at = _now()
            quality["valid_count"] = len(bars)
            quality["saved_count"] = len(bars) - quality["preserved_count"]
            quality["status"] = "partial" if quality["rejected_count"] or quality["adjustment_missing_count"] else "complete"
            run.quality = quality
            run.status = "partial" if quality["status"] == "partial" else "success"
            run.finished_at = run.published_at = _now()
            run.owner = None
            run.lease_until = None
            self.db.commit()
            return bars, quality
        except Exception as exc:
            self.db.rollback()
            self.db.execute(update(OhlcvCollectionRun).where(OhlcvCollectionRun.id == run_id,
                OhlcvCollectionRun.owner == owner).values(status="failed", owner=None,
                lease_until=None, finished_at=_now(), quality={"status":"failed", "reason":type(exc).__name__}))
            self.db.commit()
            raise

    def _published(self, run):
        """Read frozen checkpoint prices, not later mutable database rows."""
        bars = []
        for item in self.db.query(OhlcvCollectionItem).filter_by(run_id=run.id).all():
            if item.status in ("valid", "adjustment_missing") and item.payload.get("bar"):
                raw = dict(item.payload["bar"])
                raw["date"] = dt.date.fromisoformat(raw["date"])
                bars.append(OHLCVData(**raw))
        quality = dict(run.quality)
        self.db.commit()
        return bars, quality
