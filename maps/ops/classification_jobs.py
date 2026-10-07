"""Independent classification collection and durable operational diagnostics."""
from __future__ import annotations
import datetime as dt
import logging
from zoneinfo import ZoneInfo
from collections.abc import Callable
from typing import Any
from sqlalchemy.orm import Session
from maps.common.settings import MapsSettings
from maps.common.models import ClassificationRun
from maps.data.classifications import ClassificationRepository
from maps.ops.notifications import Notification
from maps.market.trading_rules import is_krx_closed_date, previous_trading_day

def is_trading_day(day: dt.date, *, extra_closed_dates: Any = ()) -> bool:
    """Calendar-only check; diagnostics must never fetch live market data."""
    return not is_krx_closed_date(day, extra_closed_dates=extra_closed_dates)

KST = ZoneInfo("Asia/Seoul")

def _local(now: dt.datetime) -> dt.datetime:
    """Normalize a clock value to naive KST."""
    return now.astimezone(KST).replace(tzinfo=None) if now.tzinfo else now

def _utc(now: dt.datetime) -> dt.datetime:
    """Convert a clock value to database UTC naive."""
    return _local(now) - dt.timedelta(hours=9)

def _deadline(settings: MapsSettings, kind: str, day: dt.date) -> dt.datetime:
    """Return the scheduled watchdog deadline in KST."""
    value = settings.maps_classification_check_time if kind == "theme" else settings.maps_data_collection_time
    hour, minute = map(int, value.split(":"))
    due = dt.datetime.combine(day, dt.time(hour, minute))
    return due if kind == "theme" else due + dt.timedelta(minutes=20)

def _serialize(run: ClassificationRun | None) -> dict | None:
    """Expose attempt evidence without membership payload duplication."""
    if run is None:
        return None
    return {"id": run.id, "status": run.status, "ref_date": run.ref_date.isoformat(),
            "provider": run.provider, "metrics": run.metrics or {}, "error": run.error,
            **{name: getattr(run, name).isoformat() if getattr(run, name) else None
               for name in ("started_at", "finished_at", "published_at", "notified_at")}}

def quality_summary(db: Session, settings: MapsSettings, now: dt.datetime) -> dict:
    """Read latest attempt independently from the last published success."""
    now = _local(now)
    summary = {}
    for kind in ("sector", "theme"):
        attempt = db.query(ClassificationRun).filter_by(kind=kind).order_by(ClassificationRun.id.desc()).first()
        success = ClassificationRepository(db).latest(kind)
        current = attempt if attempt and attempt.ref_date == now.date() else None
        if kind == "theme" and not settings.maps_theme_collection_enabled:
            state = "disabled"
        elif current:
            state = current.status
        elif not is_trading_day(now.date(), extra_closed_dates=settings.krx_closed_dates):
            state = "skipped"
        else:
            state = "pending" if now < _deadline(settings, kind, now.date()) else "missed"
        required = now.date() if is_trading_day(now.date(), extra_closed_dates=settings.krx_closed_dates) and now >= _deadline(settings, kind, now.date()) else previous_trading_day(now.date(), extra_closed_dates=settings.krx_closed_dates)
        summary[kind] = {"state": state, "stale": not success or success.ref_date != required,
                         "latest_attempt": _serialize(attempt), "last_successful": _serialize(success)}
    return summary

class ClassificationJobs:
    """Collect outside DB transactions; check persisted failures without HTTP."""
    def __init__(self, settings: MapsSettings, session_factory: Callable[[], Session], notifier: Any, adapter: Any = None, *, now: Callable[[], dt.datetime] | None = None) -> None:
        """Inject persistence, notifier, public adapter and current KST clock."""
        self.settings, self.session_factory, self.notifier, self.adapter = settings, session_factory, notifier, adapter
        self.now = now or (lambda: dt.datetime.now(KST))

    def collect(self, ref_date: dt.date) -> dict:
        """Publish theme membership only for today's verified sector universe."""
        if not self.settings.maps_theme_collection_enabled:
            return {"status": "disabled", "ref_date": ref_date.isoformat()}
        if not is_trading_day(ref_date, extra_closed_dates=self.settings.krx_closed_dates):
            return {"status": "skipped", "ref_date": ref_date.isoformat()}
        with self.session_factory() as db:
            repo = ClassificationRepository(db)
            run = repo.start("theme", "naver", ref_date)
            run_id = run.id
            sector = repo.latest("sector", ref_date=ref_date)
            expected = list(sector.expected_tickers) if sector else []
        try:
            if ref_date != _local(self.now()).date():
                raise ValueError("live_theme_requires_current_kst_date")
            if not expected:
                raise ValueError("same_day_sector_universe_unavailable")
            adapter = self.adapter
            if adapter is None:
                from maps.data.naver_themes import NaverThemeAdapter
                adapter = NaverThemeAdapter()
            payload = adapter.collect(ref_date, expected)
            with self.session_factory() as db:
                repo = ClassificationRepository(db)
                run = db.get(ClassificationRun, run_id)
                # Watchdog may have expired this attempt while HTTP was in flight.
                if run.status != "running":
                    return {"status": run.status, "error": run.error}
                run = repo.publish(run, payload)
                result = _serialize(run)
        except Exception as exc:
            with self.session_factory() as db:
                run = db.get(ClassificationRun, run_id)
                if run.status == "running":
                    ClassificationRepository(db).fail(run, str(exc))
                result = _serialize(run)
        self.check(self.now(), ref_date=ref_date, create_missing=False)
        return result

    def check(self, now: dt.datetime | None = None, *, ref_date: dt.date | None = None, create_missing: bool = True) -> list[dict]:
        """Detect missed/interrupted attempts and deliver each failure/recovery once."""
        now = _local(now or dt.datetime.now(KST))
        day = ref_date or now.date()
        previous_results = []
        if ref_date is None:
            with self.session_factory() as db:
                unresolved = db.query(ClassificationRun.ref_date).filter(
                    ClassificationRun.ref_date < day,
                    ClassificationRun.status.in_(["running", "partial", "failed", "missed"]),
                    ClassificationRun.notified_at.is_(None)).order_by(ClassificationRun.ref_date).limit(500).all()
                recoveries = db.query(ClassificationRun.ref_date).filter(
                    ClassificationRun.ref_date < day, ClassificationRun.status == "complete",
                    ClassificationRun.notified_at.is_(None)).order_by(ClassificationRun.id.desc()).limit(100).all()
            # Reconcile persisted evidence, never invent misses before rollout.
            for prior_day in sorted({row[0] for row in unresolved + recoveries}):
                previous_results.extend(self.check(now, ref_date=prior_day, create_missing=False))
        if not is_trading_day(day, extra_closed_dates=self.settings.krx_closed_dates):
            return previous_results
        checked = previous_results
        with self.session_factory() as db:
            repo = ClassificationRepository(db)
            for kind in ("sector", "theme"):
                if kind == "theme" and not self.settings.maps_theme_collection_enabled:
                    continue
                rows = db.query(ClassificationRun).filter_by(kind=kind, ref_date=day).order_by(ClassificationRun.id).all()
                due = now >= _deadline(self.settings, kind, day)
                if not rows and due and create_missing:
                    run = repo.start(kind, "naver" if kind == "theme" else "krx", day)
                    repo.fail(run, "classification_job_missed", status="missed")
                    rows = [run]
                for run in rows:
                    # Conditional update avoids clobbering a concurrent publication.
                    if run.status == "running" and due and run.started_at <= _utc(now) - dt.timedelta(minutes=20):
                        db.query(ClassificationRun).filter_by(id=run.id, status="running").update(
                            {"status": "failed", "error": "classification_job_interrupted", "finished_at": _utc(now)}, synchronize_session=False)
                        db.commit()
                        db.refresh(run)
                    failed = run.status in {"partial", "failed", "missed"}
                    previous = db.query(ClassificationRun).filter(ClassificationRun.kind == kind, ClassificationRun.id < run.id).order_by(ClassificationRun.id.desc()).first()
                    recovery = run.status == "complete" and previous and previous.status in {"partial", "failed", "missed"} and previous.notified_at is not None
                    if run.notified_at is None and (failed or recovery):
                        # Conditional write claims the row until commit/rollback. A second
                        # checker waits, then observes delivered state instead of sending.
                        claimed = db.query(ClassificationRun).filter_by(id=run.id, notified_at=None).update(
                            {"notified_at": _utc(now)}, synchronize_session=False)
                        if not claimed:
                            db.rollback()
                            db.refresh(run)
                            continue
                        try:
                            delivered = self.notifier.send(Notification(
                                level="ERROR" if failed else "INFO",
                                title=f"MAPS {kind} classification {'failed' if failed else 'recovered'}",
                                message=run.error or "classification recovered",
                                fields={"run_id": run.id, "ref_date": str(day), "status": run.status}))
                        except Exception:
                            logging.getLogger(__name__).exception("Classification notification delivery failed: %s", run.id)
                            delivered = False
                        if delivered:
                            db.commit()
                        else:
                            db.rollback()
                        db.refresh(run)
                    checked.append(_serialize(run))
        return checked
