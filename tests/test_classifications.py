"""Snapshot publication evidence and conservative validation."""
import datetime as dt
import pytest
from maps.common.exceptions import DataCollectionError


def payload(kind="theme", **changes):
    from maps.data.classifications import ClassificationPayload
    values = dict(kind=kind, provider="test", ref_date=dt.date(2026, 10, 6),
                  expected_tickers=["A", "B"], catalog={"1": "Name"},
                  memberships={"A": ["1"], "B": []}, metrics={"assigned_count": 999})
    values.update(changes)
    return ClassificationPayload(**values)


def test_publish_and_asof_no_theme_unknown_and_failed_attempt(db):
    from maps.data.classifications import ClassificationRepository
    repo = ClassificationRepository(db)
    p = payload()
    run = repo.publish(repo.start(p.kind, p.provider, p.ref_date), p)
    assert repo.memberships(run) == {"A": {"1"}, "B": set()}
    assert "C" not in repo.memberships(run, ["B", "C"])
    assert repo.theme_names(run) == {"1": "Name"}
    assert run.metrics["assigned_count"] == 1
    assert repo.latest("theme", p.ref_date) == run
    assert repo.latest("theme", dt.date(2026, 10, 5)) is None
    assert repo.latest("theme", available_at=run.published_at - dt.timedelta(seconds=1)) is None
    aware = run.published_at.replace(tzinfo=dt.timezone.utc)
    assert repo.latest("theme", available_at=aware) == run
    repo.fail(repo.start("theme", "test", p.ref_date), "outage")
    assert repo.latest("theme") == run
    with pytest.raises(DataCollectionError):
        repo.publish(run, p)


@pytest.mark.parametrize("changes", [
    {"expected_tickers": []}, {"expected_tickers": ["A", "A"]},
    {"catalog": {}}, {"catalog": {"1": " "}},
    {"memberships": {"A": ["1"]}},
    {"memberships": {"A": ["1", "1"], "B": []}},
    {"memberships": {"A": ["2"], "B": []}},
    {"memberships": {"A": ["1"], "B": [], "C": []}},
    {"kind": "sector"},
])
def test_reject_incomplete_or_duplicate_payload(db, changes):
    from maps.data.classifications import ClassificationRepository
    repo = ClassificationRepository(db)
    p = payload(**changes)
    run = repo.start(p.kind, p.provider, p.ref_date)
    with pytest.raises(DataCollectionError):
        repo.publish(run, p)
    assert repo.latest(p.kind) is None


def test_sector_collection_preserves_prices_on_missing_classification(db, monkeypatch):
    from maps.data.collector import DataCollector
    from maps.data.krx_adapter import MockKRXAdapter, InvestorFlowData
    from maps.common.models import CollectionLog, HistoricalOHLCV
    from maps.data.classifications import ClassificationRepository
    monkeypatch.setattr("maps.market.feeds.collect_market_news_sentiment", lambda *a: None)
    day = dt.date(2026, 10, 6)
    krx = MockKRXAdapter(seed_tickers=["A", "B"])
    krx.set_investor_flows({"A": InvestorFlowData(date=day, ticker="A", market="KOSPI", foreign_net_value=1, institutional_net_value=1, individual_net_value=-2)})
    krx.set_sectors({"A": "Tech", "B": "Finance"})
    result = DataCollector(krx, db).collect_daily(day)
    assert result.classification_quality["status"] == "complete"
    previous = ClassificationRepository(db).latest("sector")
    krx.set_sectors({"A": float("nan")})
    result = DataCollector(krx, db).collect_daily(day)
    assert result.classification_quality["status"] == "partial"
    assert ClassificationRepository(db).latest("sector") == previous
    assert db.query(HistoricalOHLCV).count() == 2
    log = db.query(CollectionLog).order_by(CollectionLog.id.desc()).first()
    assert log.status == "partial"
    assert log.metadata_quality["status"] == "complete"
    assert log.classification_quality == result.classification_quality


@pytest.mark.parametrize("problem", ["empty", "schema", "nan", "none", "blank", "exception", "duplicate"])
def test_strict_sector_adapter_rejects_market_and_parser_failures(monkeypatch, problem):
    import pandas as pd
    from pykrx import stock
    from maps.data.krx_adapter import KRXAdapter
    def frame(day, market):
        if market == "KOSPI":
            return pd.DataFrame({"업종명": ["Tech"]}, index=["A"])
        if problem == "exception":
            raise RuntimeError("market unavailable")
        if problem == "empty":
            return pd.DataFrame()
        if problem == "schema":
            return pd.DataFrame({"other": ["Tech"]}, index=["B"])
        label = {"nan": float("nan"), "none": None, "blank": " ", "duplicate": "Tech"}[problem]
        return pd.DataFrame({"업종명": [label]}, index=["A" if problem == "duplicate" else "B"])
    monkeypatch.setattr(stock, "get_market_sector_classifications", frame)
    with pytest.raises(DataCollectionError):
        KRXAdapter().get_sector_classifications_strict(dt.date(2026, 10, 6))


def test_additive_migration_upgrade_downgrade(db):
    import importlib.util
    from pathlib import Path
    from sqlalchemy import inspect
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    spec = importlib.util.spec_from_file_location("classification_migration", Path("alembic/versions/0039_classification_snapshots.py"))
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = db.get_bind()
    # Simulate the previous schema; this test only exercises the additive step.
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE classification_member")
        connection.exec_driver_sql("DROP TABLE classification_run")
        connection.exec_driver_sql("ALTER TABLE collection_log DROP COLUMN classification_quality")
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        assert "classification_run" in inspect(connection).get_table_names()
        assert "classification_quality" in {c["name"] for c in inspect(connection).get_columns("collection_log")}
        migration.downgrade()
        assert "classification_run" not in inspect(connection).get_table_names()
        assert "classification_quality" not in {c["name"] for c in inspect(connection).get_columns("collection_log")}


def test_strict_sector_adapter_accepts_korean_columns(monkeypatch):
    import pandas as pd
    from pykrx import stock
    from maps.data.krx_adapter import KRXAdapter
    monkeypatch.setattr(stock, "get_market_sector_classifications", lambda day, market:
        pd.DataFrame({"종목코드": ["A" if market == "KOSPI" else "B"], "업종명": ["전자"]}))
    assert KRXAdapter().get_sector_classifications_strict(dt.date(2026, 10, 6)) == {"A": "전자", "B": "전자"}


def test_stale_run_cannot_publish_after_watchdog_failure(db):
    from sqlalchemy.orm import Session
    from maps.data.classifications import ClassificationRepository
    from maps.common.models import ClassificationRun
    repo = ClassificationRepository(db)
    p = payload()
    run = repo.start(p.kind, p.provider, p.ref_date)
    with Session(db.get_bind()) as other:
        ClassificationRepository(other).fail(other.get(ClassificationRun, run.id), "timeout")
    with pytest.raises(DataCollectionError):
        repo.publish(run, p)
    db.expire_all()
    assert run.status == "failed"
    assert repo.latest("theme") is None


def test_stale_run_cannot_fail_after_publication(db):
    from sqlalchemy.orm import Session
    from maps.data.classifications import ClassificationRepository
    from maps.common.models import ClassificationRun
    repo = ClassificationRepository(db)
    p = payload()
    run = repo.start(p.kind, p.provider, p.ref_date)
    with Session(db.get_bind()) as other:
        other_repo = ClassificationRepository(other)
        other_repo.publish(other.get(ClassificationRun, run.id), p)
    with pytest.raises(DataCollectionError):
        repo.fail(run, "timeout")
    db.expire_all()
    assert run.status == "complete"
