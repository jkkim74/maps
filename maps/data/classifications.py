"""Validated immutable classification snapshots and publication boundaries."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from sqlalchemy import update
from sqlalchemy.orm import Session
from maps.common.exceptions import DataCollectionError
from maps.common.models import ClassificationRun, ClassificationMember


@dataclass
class ClassificationPayload:
    """Provider evidence with explicit entries for every expected ticker."""
    kind: str
    provider: str
    ref_date: date
    expected_tickers: list[str]
    catalog: dict[str, str]
    memberships: dict[str, list[str]]
    metrics: dict = field(default_factory=dict)
    error: str | None = None


def _utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


def valid_label(value: object) -> bool:
    """Never turn null/NaN/non-string labels into apparently valid names."""
    return isinstance(value, str) and bool(value.strip()) and value.strip().lower() not in {"nan", "none", "null"}


class ClassificationRepository:
    """Commit durable attempts independently from immutable publications."""
    def __init__(self, db: Session) -> None:
        self.db = db

    def start(self, kind: str, provider: str, ref_date: date) -> ClassificationRun:
        """Persist start evidence before contacting the provider."""
        if kind not in {"sector", "theme"} or not valid_label(provider):
            raise DataCollectionError("invalid classification kind/provider")
        run = ClassificationRun(kind=kind, provider=provider, ref_date=ref_date,
                                status="running", started_at=_utc(datetime.now(timezone.utc)),
                                expected_tickers=[], catalog={}, metrics={})
        self.db.add(run)
        self.db.commit()
        return run

    def publish(self, run: ClassificationRun, payload: ClassificationPayload) -> ClassificationRun:
        """Validate full evidence, then atomically publish relations and run."""
        p = payload
        if run.status != "running" or run.published_at is not None:
            raise DataCollectionError("classification run is immutable or no longer running")
        if (run.kind, run.provider, run.ref_date) != (p.kind, p.provider, p.ref_date) or p.error:
            raise DataCollectionError("classification payload does not match run or reports error")
        if not p.expected_tickers or len(set(p.expected_tickers)) != len(p.expected_tickers):
            raise DataCollectionError("classification universe empty or duplicate")
        if any(not valid_label(t) or len(t) > 16 or any(c.isspace() or ord(c) < 32 for c in t) for t in p.expected_tickers):
            raise DataCollectionError("invalid classification ticker")
        if not p.catalog or any(not valid_label(c) or not valid_label(n) or len(c) > 128 or len(n) > 256 for c, n in p.catalog.items()):
            raise DataCollectionError("classification catalog empty or invalid")
        if set(p.memberships) != set(p.expected_tickers):
            raise DataCollectionError("classification membership universe missing or unexpected")
        for ticker, codes in p.memberships.items():
            if not isinstance(codes, list) or len(codes) != len(set(codes)) or any(c not in p.catalog for c in codes):
                raise DataCollectionError(f"invalid or duplicate classification codes: {ticker}")
            if p.kind == "sector" and len(codes) != 1:
                raise DataCollectionError(f"sector coverage incomplete: {ticker}")
        assigned = sum(bool(codes) for codes in p.memberships.values())
        metrics = dict(p.metrics)
        metrics.update(expected_count=len(p.expected_tickers), assigned_count=assigned,
                       unassigned_count=len(p.expected_tickers) - assigned,
                       relation_count=sum(map(len, p.memberships.values())), catalog_count=len(p.catalog))
        try:
            now = _utc(datetime.now(timezone.utc))
            transitioned = self.db.execute(update(ClassificationRun).where(
                ClassificationRun.id == run.id, ClassificationRun.status == "running",
                ClassificationRun.published_at.is_(None)).values(
                expected_tickers=list(p.expected_tickers), catalog=dict(p.catalog),
                metrics=metrics, status="complete", finished_at=now, published_at=now),
                execution_options={"synchronize_session": False})
            if transitioned.rowcount != 1:
                raise DataCollectionError("classification run was closed by another worker")
            self.db.add_all(ClassificationMember(run_id=run.id, ticker=t, code=c, name=p.catalog[c])
                            for t, codes in p.memberships.items() for c in codes)
            self.db.commit()
            self.db.refresh(run)
        except Exception:
            self.db.rollback()
            raise
        return run

    def fail(self, run: ClassificationRun, error: str, status: str = "failed", metrics: dict | None = None) -> ClassificationRun:
        """Close an unpublished attempt without superseding a success."""
        if run.published_at is not None or run.status == "complete":
            raise DataCollectionError("published classification run is immutable")
        if status not in {"partial", "failed", "missed"}:
            raise DataCollectionError("invalid classification failure status")
        values = dict(status=status, error=error, finished_at=_utc(datetime.now(timezone.utc)))
        if metrics is not None:
            values["metrics"] = dict(metrics)
        try:
            transitioned = self.db.execute(update(ClassificationRun).where(
                ClassificationRun.id == run.id, ClassificationRun.status == "running",
                ClassificationRun.published_at.is_(None)).values(**values),
                execution_options={"synchronize_session": False})
            if transitioned.rowcount != 1:
                raise DataCollectionError("classification run was closed by another worker")
            self.db.commit()
            self.db.refresh(run)
        except Exception:
            self.db.rollback()
            raise
        return run

    def latest(self, kind: str, ref_date: date | None = None, available_at: datetime | None = None) -> ClassificationRun | None:
        """Read a complete publication, optionally at an exact day/as-of time."""
        query = self.db.query(ClassificationRun).filter(ClassificationRun.kind == kind,
                   ClassificationRun.status == "complete", ClassificationRun.published_at.isnot(None))
        if ref_date is not None:
            query = query.filter(ClassificationRun.ref_date == ref_date)
        if available_at is not None:
            query = query.filter(ClassificationRun.published_at <= _utc(available_at))
        return query.order_by(ClassificationRun.ref_date.desc(), ClassificationRun.published_at.desc(), ClassificationRun.id.desc()).first()

    def memberships(self, run: ClassificationRun, tickers: list[str] | None = None) -> dict[str, set[str]]:
        """Known empty themes stay empty; outside-universe tickers stay unknown."""
        selected = set(run.expected_tickers) if tickers is None else set(run.expected_tickers).intersection(tickers)
        result = {ticker: set() for ticker in selected}
        for member in self.db.query(ClassificationMember).filter_by(run_id=run.id):
            if member.ticker in result:
                result[member.ticker].add(member.code)
        return result

    def theme_names(self, run: ClassificationRun) -> dict[str, str]:
        """Return historical snapshot catalog rather than current metadata."""
        return dict(run.catalog)
