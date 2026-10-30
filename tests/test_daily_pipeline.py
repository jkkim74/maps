"""Durable daily dependency claims never replay completed business work."""
import datetime as dt

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from maps.common.db import Base
from maps.common.models import DailyPipelineStage
from maps.ops.daily_pipeline import DailyPipeline


DAY = dt.date(2026, 10, 30)
NOW = dt.datetime(2026, 10, 30, 8, 0)  # 17:00 KST


def factory():
    """Use separate sessions against one isolated in-memory database."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def pipeline(sessions):
    """Configure existing production times as minimum start times."""
    return DailyPipeline(sessions, {
        "data_collection": "16:40", "candidate_generation": "16:50", "validation": "17:10",
    })


def test_waits_for_time_then_dependency_and_deduplicates_after_restart():
    sessions = factory()
    work = pipeline(sessions)
    assert work.claim("candidate_generation", DAY, NOW).reason == "dependency_pending"
    claim = work.claim("data_collection", DAY, NOW)
    assert claim.acquired
    assert not pipeline(sessions).claim("data_collection", DAY, NOW).acquired
    work.finish(claim, "partial", {"saved_count": 12}, now=NOW)
    candidate = pipeline(sessions).claim("candidate_generation", DAY, NOW)
    assert candidate.acquired
    work.finish(candidate, "success", {}, now=NOW)
    assert work.claim("validation", DAY, NOW).reason == "before_minimum_time"
    later = NOW + dt.timedelta(minutes=15)
    validation = work.claim("validation", DAY, later)
    assert validation.acquired
    work.finish(validation, "success", {}, now=later)
    assert pipeline(sessions).claim("validation", DAY, later).reason == "already_finished"


def test_failed_dependency_blocks_downstream_and_empty_collection_cannot_pass():
    sessions = factory()
    work = pipeline(sessions)
    claim = work.claim("data_collection", DAY, NOW)
    work.finish(claim, "failed", {"saved_count": 0}, now=NOW)
    assert work.claim("candidate_generation", DAY, NOW).reason == "dependency_failed"


def test_stale_owner_cannot_finish_and_expired_business_stage_is_not_replayed():
    sessions = factory()
    work = pipeline(sessions)
    claim = work.claim("data_collection", DAY, NOW)
    work.finish(claim, "success", {}, now=NOW)
    candidate = work.claim("candidate_generation", DAY, NOW)
    with sessions() as db:
        row = db.query(DailyPipelineStage).filter_by(name="candidate_generation").one()
        row.lease_until = NOW - dt.timedelta(seconds=1)
        db.commit()
    assert work.claim("candidate_generation", DAY, NOW).reason == "interrupted_requires_review"
    assert not work.finish(candidate, "success", {}, now=NOW)
    assert work.claim("validation", DAY, NOW + dt.timedelta(hours=1)).reason == "dependency_failed"


def test_wrong_reference_date_cannot_start_current_business_work():
    work = pipeline(factory())
    assert work.claim("data_collection", DAY - dt.timedelta(days=1), NOW).reason == "date_mismatch"


def test_interrupted_collection_resumes_with_new_owner_and_fences_old_worker():
    sessions = factory()
    work = pipeline(sessions)
    old = work.claim("data_collection", DAY, NOW)
    new = work.claim("data_collection", DAY, NOW + dt.timedelta(minutes=2))
    assert new.acquired and new.owner != old.owner
    assert not work.finish(old, "success", {}, now=NOW)
    assert work.finish(new, "partial", {}, now=NOW)


def test_close_report_exposes_pending_collection_without_claiming_completion(monkeypatch):
    from maps.common.settings import MapsSettings
    from maps.ops.scheduler import OperationalPipeline
    from types import SimpleNamespace

    sessions = factory()
    with sessions() as db:
        db.add(DailyPipelineStage(ref_date=DAY, name="data_collection", status="running"))
        db.commit()
    sent = []
    monkeypatch.setattr("maps.ops.close_report.build_close_report", lambda *args: "report")
    monkeypatch.setattr("maps.ops.notifications.get_telegram_notifier", lambda: SimpleNamespace(
        enabled=True, send_long=lambda message: sent.append(message) or True))
    work = OperationalPipeline(settings=MapsSettings(maps_data_provider="pykrx"), session_factory=sessions)
    result = work.run_daily_close_report(DAY)
    assert result.status == "success"
    assert "data_collection=running" in sent[0]
    assert "candidate_generation=pending" in sent[0]


def test_scheduler_manual_and_watchdog_share_completed_claims(monkeypatch):
    from types import SimpleNamespace
    from maps.common.settings import MapsSettings
    from maps.ops.scheduler import JobRun, MapsOperationalScheduler, OperationalPipeline

    sessions = factory()
    settings = MapsSettings(maps_data_provider="pykrx", maps_data_collection_time="00:00",
                            maps_candidate_time="00:00", maps_validation_time="00:00")
    work = OperationalPipeline(settings=settings, session_factory=sessions)
    calls = []
    def run(name):
        calls.append(name)
        now = dt.datetime.now(dt.timezone.utc)
        return JobRun(name, "success", now, now, "ok", {})
    monkeypatch.setattr(work, "collect_data", lambda: run("data_collection"))
    monkeypatch.setattr(work, "generate_candidates", lambda: run("candidate_generation"))
    monkeypatch.setattr(work, "run_validation", lambda: run("validation"))
    monkeypatch.setattr("maps.data.ohlcv_runs.load_collection", lambda *args: SimpleNamespace(ohlcv=[1]))
    monkeypatch.setattr("maps.ops.scheduler._is_krx_market_day", lambda *args: True)
    scheduler = MapsOperationalScheduler(settings=settings, pipeline=work)
    assert scheduler.run_once("candidate_generation").message == "dependency_pending"
    assert calls == []
    scheduler._advance_daily_pipeline()
    assert calls == ["data_collection", "candidate_generation", "validation"]
    restarted = MapsOperationalScheduler(settings=settings, pipeline=work)
    restarted._advance_daily_pipeline()
    assert len(calls) == 3
    assert restarted.status()["daily_pipeline"]["validation"]["status"] == "success"


def test_candidate_generation_does_not_collect_when_snapshot_is_absent(monkeypatch):
    from maps.common.settings import MapsSettings
    from maps.ops.scheduler import OperationalPipeline

    work = OperationalPipeline(settings=MapsSettings(maps_data_provider="pykrx"), session_factory=factory())
    def forbidden(*args):
        raise AssertionError("candidate generation must not fetch prices")
    monkeypatch.setattr("maps.data.collector.DataCollector.collect_daily", forbidden)
    monkeypatch.setattr("maps.data.ohlcv_runs.load_collection", lambda *args: None)
    result = work.generate_candidates(DAY)
    assert result.status == "skipped"
    assert result.message == "collection_pending"
