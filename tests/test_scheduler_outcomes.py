"""Scheduler outcome and durable recording regressions; all services stay offline."""

import datetime as dt
import json
import logging
from types import SimpleNamespace

import pytest

from maps.common.models import CandidateSnapshot, CollectionLog, JobRunLog
from maps.common.settings import MapsSettings
from maps.ops.scheduler import JobRun, MapsOperationalScheduler, OperationalPipeline
from tests.test_scheduler import _session_factory


@pytest.fixture
def scheduler():
    engine, factory = _session_factory()
    settings = MapsSettings(maps_broker_mode="mock", maps_live_trading_enabled=False,
                            maps_dry_run=False, maps_fujimoto_enabled=False, dart_api_key="")
    pipeline = OperationalPipeline(settings=settings, session_factory=factory)
    yield MapsOperationalScheduler(settings=settings, pipeline=pipeline)
    engine.dispose()


@pytest.mark.parametrize("name,reason", [
    ("validation", "promotion_inputs_missing"),
    ("order_cycle", "execution_disabled"),
    ("eod_cleanup", "execution_disabled"),
    ("daily_close_report", "telegram_disabled"),
    ("limit_up_after_hours", "limit_up_mode"),
    ("fujimoto_screen", "fujimoto_disabled"),
    ("dart_financial_collection", "missing_api_key"),
])
def test_real_disabled_or_missing_input_jobs_are_skipped(scheduler, monkeypatch, name, reason):
    monkeypatch.setattr("maps.ops.notifications.get_telegram_notifier", lambda: SimpleNamespace(enabled=False))
    run = scheduler.run_once(name)
    assert run.status == "skipped"
    assert run.message == reason
    with scheduler._pipeline._session_factory() as db:
        row = db.query(JobRunLog).one()
        assert row.status == "skipped"
        assert row.message == reason


def test_order_cycle_dry_run_is_skipped(scheduler):
    scheduler._settings.maps_live_trading_enabled = True
    scheduler._settings.maps_dry_run = True
    run = scheduler.run_once("order_cycle")
    assert run.status == "skipped"
    assert run.message == "dry_run"


def test_evaluation_with_zero_passes_still_succeeds(scheduler, monkeypatch):
    monkeypatch.setattr(scheduler._pipeline, "_generate_validation_metrics", lambda *args: {})
    monkeypatch.setattr(scheduler._pipeline, "_evaluate_promotions", lambda *args: {
        "evaluated": 2, "passed": 0, "failed": 2, "strategies": []})
    assert scheduler.run_once("validation").status == "success"


def test_real_dart_mapping_failure_remains_partial(scheduler, monkeypatch):
    from maps.data.dart_financials import DartStop
    scheduler._settings.dart_api_key = "offline-test"
    with scheduler._pipeline._session_factory() as db:
        db.add(CandidateSnapshot(ticker="005930", name="fixture", market="KOSPI", ref_date=dt.date.today(),
                                 strategy_id="contrarian_quality_accumulation_v1"))
        db.commit()
    def stop():
        raise DartStop("budget_exhausted")
    monkeypatch.setattr("maps.data.dart_financials.DartClient", lambda *args: SimpleNamespace(corp_codes=stop, requests=0))
    run = scheduler.run_once("dart_financial_collection")
    assert run.status == "partial"
    assert run.message == "budget_exhausted"


@pytest.mark.parametrize("annual_status", ["partial", "success"])
def test_real_screen_date_is_consistent_in_log_api_and_restart(scheduler, monkeypatch, caplog, annual_status):
    from maps.fujimoto.sources import screen_market
    now = dt.datetime(2026, 10, 22, 13, tzinfo=dt.timezone.utc)
    monkeypatch.setattr("maps.fujimoto.sources.datetime", SimpleNamespace(now=lambda *args: now))
    monkeypatch.setattr("maps.fujimoto.sources.screen_market", lambda db, day: screen_market(db, day, now=now))
    monkeypatch.setattr("maps.fujimoto.sources.AnnualCollector", lambda *args, **kwargs: SimpleNamespace(collect=lambda *args, **kwargs: {"status": annual_status}))
    monkeypatch.setattr("maps.fujimoto.valuation.EPSCollector", lambda *args, **kwargs: SimpleNamespace(collect=lambda *args, **kwargs: {"status": "success"}))
    monkeypatch.setattr("maps.fujimoto.valuation.collect_metadata", lambda *args: {"status": "success"})
    scheduler._settings.maps_fujimoto_enabled = True
    caplog.set_level(logging.INFO, logger="maps.ops.scheduler")
    run = scheduler._record("fujimoto_screen", lambda: scheduler._pipeline.run_fujimoto_screen(now.date()))
    assert run.status == "partial"
    assert run.details["screen"]["ref_date"] == "2026-10-22"
    assert "2026-10-22" in caplog.text
    with scheduler._pipeline._session_factory() as db:
        row = db.query(JobRunLog).one()
        assert json.loads(row.details_json) == run.details
        assert row.status == "partial"
    restarted = MapsOperationalScheduler(settings=scheduler._settings, pipeline=scheduler._pipeline)
    assert restarted.status()["last_runs"]["fujimoto_screen"] == scheduler.status()["last_runs"]["fujimoto_screen"]


@pytest.mark.parametrize("bad", [object(), float("nan"), {1: "non-string-key"}])
def test_unrecordable_result_fails_explicitly_without_replaying_work(scheduler, bad):
    calls = []
    def work():
        calls.append(1)
        return JobRun("validation", "success", dt.datetime.now(dt.timezone.utc), details={"nested": [bad]})
    run = scheduler._record("validation", work)
    assert calls == [1]
    assert run.status == "failed"
    assert "details.nested[0]" in run.message
    with scheduler._pipeline._session_factory() as db:
        row = db.query(JobRunLog).one()
        assert row.status == "failed"
        assert json.loads(row.details_json) == run.details


def test_closed_market_job_records_durable_skip(scheduler, monkeypatch):
    monkeypatch.setattr("maps.ops.scheduler._is_krx_market_day", lambda: False)
    scheduler._make_krx_job("validation")()
    run = scheduler.status()["last_runs"]["validation"]
    assert run["status"] == "skipped"
    assert run["message"] == "krx_market_closed"


def test_restart_restores_latest_durable_outcome(scheduler):
    scheduler.run_once("validation")
    restarted = MapsOperationalScheduler(settings=scheduler._settings, pipeline=scheduler._pipeline)
    assert restarted.status()["last_runs"] == scheduler.status()["last_runs"]


def test_after_hours_final_job_keeps_its_identity_after_restart(scheduler):
    scheduler.run_once("limit_up_after_hours_final")
    restarted = MapsOperationalScheduler(settings=scheduler._settings, pipeline=scheduler._pipeline)
    assert "limit_up_after_hours_final" in restarted.status()["last_runs"]


def test_nested_dates_and_datetime_are_normalized_before_recording(scheduler):
    stamp = dt.datetime(2026, 10, 22, 1, 2, tzinfo=dt.timezone.utc)
    run = scheduler._record("validation", lambda: JobRun(
        "validation", "success", stamp, details={"rows": ({"date": stamp.date(), "seen": stamp},)}))
    assert run.details == {"rows": [{"date": "2026-10-22", "seen": "2026-10-22T01:02:00+00:00"}]}


@pytest.mark.parametrize("raw", ['{"metric":NaN}', '"unexpected scalar"', '{invalid'])
def test_invalid_legacy_details_restore_as_explicit_failed_recording(scheduler, raw):
    with scheduler._pipeline._session_factory() as db:
        db.add(JobRunLog(name="validation", status="success", ref_date=dt.date.today(),
                         started_at=dt.datetime.now(dt.timezone.utc), details_json=raw))
        db.commit()
    restarted = MapsOperationalScheduler(settings=scheduler._settings, pipeline=scheduler._pipeline)
    result = restarted.status()["last_runs"]["validation"]
    assert result["status"] == "failed"
    assert result["message"].startswith("persisted_details_invalid:")
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("successful,expected", [(0, "failed"), (1, "partial")])
def test_incomplete_fundamental_backfill_is_not_success(scheduler, monkeypatch, successful, expected):
    monkeypatch.setattr("maps.ops.scheduler.DataCollector.collect_fundamental_history", lambda *args: {
        "rows": successful, "failed_days": 1, "success_days": successful})
    run = scheduler.backfill_fundamentals(dt.date(2026, 10, 20), dt.date(2026, 10, 21))
    assert run.status == expected


@pytest.mark.parametrize("name", ["broker_sync", "classification_check"])
@pytest.mark.parametrize("heartbeat_offset,expected", [(-1, "failed"), (1, "success")])
def test_restart_uses_only_newer_heartbeat_to_replace_old_failure(scheduler, name, heartbeat_offset, expected):
    stamp = dt.datetime(2026, 10, 22, 1, tzinfo=dt.timezone.utc)
    with scheduler._pipeline._session_factory() as db:
        db.add(JobRunLog(name=name, status="failed", ref_date=stamp.date(), started_at=stamp,
                         finished_at=stamp, message="old failure", details_json="{}"))
        db.add(CollectionLog(source=f"scheduler.{name}", status="success", ref_date=stamp.date(),
                             items=3, note="completed heartbeat", created_at=stamp + dt.timedelta(minutes=heartbeat_offset)))
        db.commit()
    restarted = MapsOperationalScheduler(settings=scheduler._settings, pipeline=scheduler._pipeline)
    result = restarted.status()["last_runs"][name]
    assert result["status"] == expected
    if expected == "success":
        assert result["message"] == "completed heartbeat"
        assert result["details"]["restored_from_heartbeat"] is True
        assert result["details"]["items"] == 3


def test_classification_success_records_one_bounded_heartbeat(scheduler, monkeypatch):
    stamp = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=2)
    scheduler._record("classification_check", lambda: JobRun("classification_check", "failed", stamp, stamp))
    monkeypatch.setattr(scheduler._pipeline, "_classification_jobs", lambda: SimpleNamespace(check=lambda: []))
    scheduler.run_once("classification_check")
    scheduler.run_once("classification_check")
    with scheduler._pipeline._session_factory() as db:
        assert db.query(CollectionLog).filter_by(source="scheduler.classification_check").count() == 1
        assert db.query(JobRunLog).filter_by(name="classification_check").count() == 1
    restarted = MapsOperationalScheduler(settings=scheduler._settings, pipeline=scheduler._pipeline)
    assert restarted.status()["last_runs"]["classification_check"]["status"] == "success"


@pytest.mark.parametrize("intervening", ["failed", "partial", "success_heartbeat"])
def test_market_closed_skip_transition_survives_restart_after_intervening_outcome(scheduler, intervening):
    """Dedup only consecutive closure skips, preserving a later return to skipped."""
    stamp = dt.datetime(2026, 10, 24, 1, tzinfo=dt.timezone.utc)
    def record(status, minute, message):
        at = stamp + dt.timedelta(minutes=minute)
        return scheduler._record("broker_sync", lambda: JobRun("broker_sync", status, at, at, message))
    record("skipped", 0, "krx_market_closed")
    if intervening == "success_heartbeat":
        with scheduler._pipeline._session_factory() as db:
            db.add(CollectionLog(source="scheduler.broker_sync", status="success", ref_date=stamp.date(),
                                 items=0, created_at=stamp + dt.timedelta(minutes=1), note="synced"))
            db.commit()
    else:
        record(intervening, 1, "sync problem")
    record("skipped", 2, "krx_market_closed")
    record("skipped", 3, "krx_market_closed")
    assert scheduler.status()["last_runs"]["broker_sync"]["status"] == "skipped"
    restarted = MapsOperationalScheduler(settings=scheduler._settings, pipeline=scheduler._pipeline)
    restored = restarted.status()["last_runs"]["broker_sync"]
    assert restored["status"] == "skipped"
    assert restored["message"] == "krx_market_closed"
    assert restored["started_at"] == "2026-10-24T01:02:00+00:00"
    with scheduler._pipeline._session_factory() as db:
        assert db.query(JobRunLog).filter_by(name="broker_sync", status="skipped").count() == 2
        if intervening != "success_heartbeat":
            assert db.query(JobRunLog).filter_by(name="broker_sync", status=intervening).count() == 1
