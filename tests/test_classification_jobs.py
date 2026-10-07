"""Classification operational regression tests; all HTTP and notifications are fakes."""
import datetime as dt
from unittest.mock import Mock
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from maps.common.db import Base
from maps.common.settings import MapsSettings

DAY = dt.date(2026, 10, 7)

@pytest.fixture
def factory():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()

def test_defaults_and_validation():
    settings = MapsSettings(_env_file=None)
    assert settings.maps_theme_collection_enabled is False
    assert settings.maps_classification_snapshot_enforced is False
    with pytest.raises(ValueError):
        MapsSettings(_env_file=None, maps_theme_collection_time="25:00")

def test_disabled_never_calls_adapter(factory):
    from maps.ops.classification_jobs import ClassificationJobs
    adapter = Mock()
    job = ClassificationJobs(MapsSettings(_env_file=None), factory, Mock(), adapter, now=lambda: dt.datetime(2026,10,7,18))
    assert job.collect(DAY)["status"] == "disabled"
    adapter.collect.assert_not_called()

def test_missing_universe_failure_dedup_and_retry(factory):
    from maps.ops.classification_jobs import ClassificationJobs
    from maps.common.models import ClassificationRun
    notifier = Mock()
    notifier.send.return_value = False
    settings = MapsSettings(_env_file=None, maps_theme_collection_enabled=True)
    job = ClassificationJobs(settings, factory, notifier, Mock(), now=lambda: dt.datetime(2026,10,7,18))
    result = job.collect(DAY)
    assert result["status"] == "failed"
    job.check(dt.datetime(2026, 10, 7, 18))
    assert notifier.send.call_count >= 1
    notifier.send.return_value = True
    job.check(dt.datetime(2026, 10, 7, 18))
    count = notifier.send.call_count
    job.check(dt.datetime(2026, 10, 7, 18))
    assert notifier.send.call_count == count
    with factory() as db:
        assert db.query(ClassificationRun).filter_by(kind="theme").first().notified_at is not None

def test_same_day_sector_only_and_publication(factory):
    from maps.ops.classification_jobs import ClassificationJobs
    from maps.data.classifications import ClassificationRepository, ClassificationPayload
    adapter = Mock()
    adapter.collect.return_value = ClassificationPayload("theme", "naver", DAY, ["A"], {"T":"Theme"}, {"A":["T"]})
    with factory() as db:
        repo = ClassificationRepository(db)
        repo.publish(repo.start("sector", "krx", DAY), ClassificationPayload("sector", "krx", DAY, ["A"], {"S":"Sector"}, {"A":["S"]}))
    job = ClassificationJobs(MapsSettings(_env_file=None, maps_theme_collection_enabled=True), factory, Mock(), adapter, now=lambda: dt.datetime(2026,10,7,18))
    assert job.collect(DAY)["status"] == "complete"
    adapter.collect.assert_called_once_with(DAY, ["A"])

def test_summary_and_holiday(factory):
    from maps.ops.classification_jobs import ClassificationJobs, quality_summary
    settings = MapsSettings(_env_file=None)
    with factory() as db:
        summary = quality_summary(db, settings, dt.datetime(2026,10,7,18))
        assert summary["theme"]["state"] == "disabled"
        assert summary["sector"]["state"] == "missed"
    job = ClassificationJobs(settings, factory, Mock(), Mock())
    assert job.check(dt.datetime(2026,10,11,18)) == []

def test_interrupted_attempt_and_recovery_once(factory):
    from maps.ops.classification_jobs import ClassificationJobs
    from maps.data.classifications import ClassificationRepository, ClassificationPayload
    from maps.common.models import ClassificationRun
    settings = MapsSettings(_env_file=None, maps_theme_collection_enabled=True)
    notifier = Mock()
    notifier.send.return_value = True
    with factory() as db:
        repo = ClassificationRepository(db)
        run = repo.start("theme", "naver", DAY)
        run.started_at = dt.datetime(2026, 10, 7, 8)
        db.commit()
    job = ClassificationJobs(settings, factory, notifier, Mock(), now=lambda: dt.datetime(2026,10,7,18))
    job.check(dt.datetime(2026, 10, 7, 18))
    with factory() as db:
        run = db.query(ClassificationRun).filter_by(kind="theme").first()
        assert run.status == "failed"
        assert run.error == "classification_job_interrupted"
        repo = ClassificationRepository(db)
        repo.publish(repo.start("theme", "naver", DAY), ClassificationPayload("theme", "naver", DAY, ["A"], {"T":"Theme"}, {"A":[]}))
    before = notifier.send.call_count
    job.check(dt.datetime(2026,10,7,18))
    assert notifier.send.call_count == before + 1
    job.check(dt.datetime(2026,10,7,18))
    assert notifier.send.call_count == before + 1

def test_adapter_failure_preserves_prior_publication(factory):
    from maps.ops.classification_jobs import ClassificationJobs, quality_summary
    from maps.data.classifications import ClassificationRepository, ClassificationPayload
    adapter = Mock()
    adapter.collect.side_effect = ValueError("source changed")
    settings = MapsSettings(_env_file=None, maps_theme_collection_enabled=True)
    with factory() as db:
        repo = ClassificationRepository(db)
        repo.publish(repo.start("sector", "krx", DAY), ClassificationPayload("sector", "krx", DAY, ["A"], {"S":"Sector"}, {"A":["S"]}))
        old = repo.publish(repo.start("theme", "naver", DAY), ClassificationPayload("theme", "naver", DAY, ["A"], {"T":"Theme"}, {"A":["T"]}))
        old_id = old.id
    job = ClassificationJobs(settings, factory, Mock(), adapter, now=lambda: dt.datetime(2026,10,7,18))
    assert job.collect(DAY)["status"] == "failed"
    with factory() as db:
        summary = quality_summary(db, settings, dt.datetime(2026,10,7,18))
        assert summary["theme"]["latest_attempt"]["error"] == "source changed"
        assert summary["theme"]["last_successful"]["id"] == old_id

def test_batch_api_quality_additive(factory, monkeypatch):
    from maps.api import batch_monitor
    monkeypatch.setattr(batch_monitor, "_now", lambda: dt.datetime(2026,10,7,18))
    monkeypatch.setattr(batch_monitor, "_is_krx_market_day", lambda day: True)
    with factory() as db:
        response = batch_monitor.get_batch_monitor(days=1, db=db)
    assert response.classification_quality["theme"]["state"] == "disabled"
    theme = next(row for row in response.jobs if row.name == "theme_collection")
    assert theme.cells[0].status == "disabled"

def test_real_adapter_provider_contract(factory):
    from maps.ops.classification_jobs import ClassificationJobs
    from maps.data.classifications import ClassificationRepository, ClassificationPayload
    from maps.data.naver_themes import NaverThemeAdapter
    from tests.test_naver_themes import Clock, Session, theme, member
    clock = Clock()
    adapter = NaverThemeAdapter(session=Session([[theme()], [member()], [theme()]], clock), sleep=clock.sleep, monotonic=clock.monotonic)
    with factory() as db:
        repo = ClassificationRepository(db)
        repo.publish(repo.start("sector", "krx", DAY), ClassificationPayload("sector", "krx", DAY, ["005930"], {"S":"Sector"}, {"005930":["S"]}))
    result = ClassificationJobs(MapsSettings(_env_file=None, maps_theme_collection_enabled=True), factory, Mock(), adapter, now=lambda: dt.datetime(2026,10,7,18)).collect(DAY)
    assert result["status"] == "complete"
    assert result["provider"] == "naver"

def test_previous_day_is_current_before_due(factory):
    from maps.ops.classification_jobs import quality_summary
    from maps.data.classifications import ClassificationRepository, ClassificationPayload
    yesterday = DAY - dt.timedelta(days=1)
    with factory() as db:
        repo = ClassificationRepository(db)
        repo.publish(repo.start("sector", "krx", yesterday), ClassificationPayload("sector", "krx", yesterday, ["A"], {"S":"Sector"}, {"A":["S"]}))
        assert quality_summary(db, MapsSettings(_env_file=None), dt.datetime(2026,10,7,9))["sector"]["stale"] is False

def test_notifier_exception_does_not_mark_delivered(factory):
    from maps.ops.classification_jobs import ClassificationJobs
    from maps.common.models import ClassificationRun
    notifier = Mock()
    notifier.send.side_effect = RuntimeError("transport unavailable")
    job = ClassificationJobs(MapsSettings(_env_file=None), factory, notifier, Mock(), now=lambda: dt.datetime(2026,10,7,18))
    job.check(dt.datetime(2026,10,7,18))
    with factory() as db:
        assert db.query(ClassificationRun).first().notified_at is None

def test_startup_expires_previous_day_attempt(factory):
    from maps.ops.classification_jobs import ClassificationJobs
    from maps.data.classifications import ClassificationRepository
    from maps.common.models import ClassificationRun
    settings = MapsSettings(_env_file=None, maps_theme_collection_enabled=True)
    with factory() as db:
        run = ClassificationRepository(db).start("theme", "naver", DAY-dt.timedelta(days=1))
        run.started_at = dt.datetime(2026,10,6,8)
        db.commit()
    ClassificationJobs(settings, factory, Mock(), Mock()).check(dt.datetime(2026,10,7,9))
    with factory() as db:
        assert db.query(ClassificationRun).first().status == "failed"

def test_watchdog_runs_periodically_without_collection(factory):
    from maps.ops.scheduler import MapsOperationalScheduler, OperationalPipeline
    pipeline = OperationalPipeline(settings=MapsSettings(_env_file=None), session_factory=factory, notifier=Mock())
    scheduler = MapsOperationalScheduler(settings=pipeline._settings, pipeline=pipeline)
    scheduler._register_jobs()
    assert scheduler._scheduler.get_job("classification_watchdog") is not None

def test_collection_rejects_historical_date(factory):
    from maps.ops.classification_jobs import ClassificationJobs
    adapter = Mock()
    job = ClassificationJobs(MapsSettings(_env_file=None, maps_theme_collection_enabled=True), factory, Mock(), adapter, now=lambda: dt.datetime(2026,10,8,9))
    result = job.collect(DAY)
    assert result["status"] == "failed"
    assert result["error"] == "live_theme_requires_current_kst_date"
    adapter.collect.assert_not_called()

def test_startup_reconciles_multiday_outage(factory):
    from maps.ops.classification_jobs import ClassificationJobs
    from maps.data.classifications import ClassificationRepository
    from maps.common.models import ClassificationRun
    with factory() as db:
        run = ClassificationRepository(db).start("theme", "naver", dt.date(2026,10,2))
        run.started_at = dt.datetime(2026,10,2,8)
        db.commit()
    ClassificationJobs(MapsSettings(_env_file=None, maps_theme_collection_enabled=True), factory, Mock(), Mock()).check(dt.datetime(2026,10,7,9))
    with factory() as db:
        assert db.query(ClassificationRun).first().status == "failed"

def test_concurrent_checks_deliver_once(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from maps.ops.classification_jobs import ClassificationJobs
    from maps.data.classifications import ClassificationRepository
    engine = create_engine(f"sqlite:///{tmp_path / 'notifications.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        repo = ClassificationRepository(db)
        repo.fail(repo.start("sector", "krx", DAY), "source unavailable")
    entered, release = Event(), Event()
    notifier = Mock()
    def deliver(_notification):
        entered.set()
        assert release.wait(5)
        return True
    notifier.send.side_effect = deliver
    job = ClassificationJobs(MapsSettings(_env_file=None), factory, notifier)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(job.check, dt.datetime(2026,10,7,18))
        assert entered.wait(5)
        second = pool.submit(job.check, dt.datetime(2026,10,7,18))
        release.set()
        first.result(timeout=10)
        second.result(timeout=10)
    assert notifier.send.call_count == 1
    engine.dispose()
