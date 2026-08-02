"""SCR-21 배치 모니터 API 테스트.

날짜·시각과 KRX 거래일 판정은 라우터 모듈에 monkeypatch해 고정한다.
기준: 2026-08-03(월)~08-07(금)이 거래일, 08-01(토)/08-02(일)은 비거래일.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from maps.api import batch_monitor as bm
from maps.common.db import Base
from maps.common.models import AnalysisRun, CollectionLog, JobRunLog, StockReportRun
from maps.common.settings import MapsSettings

_KST_OFFSET = dt.timedelta(hours=9)

# 수요일 20:00 KST — 모든 잡의 예정 시각+grace가 지난 시점
_NOW = dt.datetime(2026, 8, 5, 20, 0)
_TODAY = _NOW.date()
_SUNDAY = dt.date(2026, 8, 2)


def _utc(kst: dt.datetime) -> dt.datetime:
    """KST naive datetime → 모델 created_at 관례(naive UTC)."""
    return kst - _KST_OFFSET


@pytest.fixture
def client(monkeypatch, tmp_path: Path):
    from main import app
    from maps.api.deps import get_db

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    monkeypatch.setattr(bm, "_now", lambda: _NOW)
    monkeypatch.setattr(bm, "_is_krx_market_day", lambda d: d.weekday() < 5)
    settings = MapsSettings(maps_blog_dir=str(tmp_path))
    monkeypatch.setattr(bm, "get_settings", lambda: settings)

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app, raise_server_exceptions=True), factory, tmp_path

    app.dependency_overrides.pop(get_db, None)
    Base.metadata.drop_all(engine)
    engine.dispose()


def _get_job(body: dict, name: str) -> dict:
    return next(j for j in body["jobs"] if j["name"] == name)


def _get_cell(body: dict, name: str, date: dt.date) -> dict:
    return next(c for c in _get_job(body, name)["cells"] if c["date"] == date.isoformat())


def test_pipeline_cells_from_job_run_log(client) -> None:
    """job_run_log 행이 success/failed 셀로, 거래일 무기록은 missed로 나온다."""
    tc, factory, _ = client
    db = factory()
    started = _utc(dt.datetime(2026, 8, 5, 17, 10))
    db.add(
        JobRunLog(
            name="validation", status="success", ref_date=_TODAY,
            started_at=started, finished_at=started + dt.timedelta(seconds=42),
        )
    )
    db.add(
        JobRunLog(
            name="data_collection", status="failed", ref_date=dt.date(2026, 8, 4),
            started_at=started, message="pykrx down",
        )
    )
    db.commit()
    db.close()

    body = tc.get("/api/v1/batch-monitor?days=3").json()

    ok = _get_cell(body, "validation", _TODAY)
    assert ok["status"] == "success"
    assert ok["duration_sec"] == 42.0
    failed = _get_cell(body, "data_collection", dt.date(2026, 8, 4))
    assert failed["status"] == "failed"
    assert failed["message"] == "pykrx down"
    # 거래일인데 기록 없음 + 예정 시각 경과 → missed
    assert _get_cell(body, "order_cycle", _TODAY)["status"] == "missed"


def test_non_trading_day_is_skipped_except_stock_report(client) -> None:
    """일요일은 skipped — 단 stock_report는 주말에도 돌므로 missed."""
    tc, _, _ = client

    body = tc.get("/api/v1/batch-monitor?days=4").json()  # 8/5~8/2(일)

    assert _get_cell(body, "validation", _SUNDAY)["status"] == "skipped"
    assert _get_cell(body, "blog", _SUNDAY)["status"] == "skipped"
    assert _get_cell(body, "stock_report", _SUNDAY)["status"] == "missed"


def test_today_before_schedule_is_pending(client, monkeypatch) -> None:
    """오늘 예정 시각+grace 전이면 pending, 지난 잡은 missed."""
    tc, _, _ = client
    # 09:30 — order_cycle(08:55+30m=09:25)은 지났고 validation(16:40+60m)은 전
    monkeypatch.setattr(bm, "_now", lambda: dt.datetime(2026, 8, 5, 9, 30))

    body = tc.get("/api/v1/batch-monitor?days=1").json()

    assert _get_cell(body, "validation", _TODAY)["status"] == "pending"
    assert _get_cell(body, "order_cycle", _TODAY)["status"] == "missed"


def test_analyze_cells_from_analysis_run(client) -> None:
    tc, factory, _ = client
    db = factory()
    db.add(AnalysisRun(ref_date=dt.date(2026, 8, 4), status="completed", picks_count=3))
    db.add(
        AnalysisRun(
            ref_date=dt.date(2026, 8, 3), status="failed", error_message="claude timeout"
        )
    )
    db.commit()
    db.close()

    body = tc.get("/api/v1/batch-monitor?days=3").json()

    ok = _get_cell(body, "analyze", dt.date(2026, 8, 4))
    assert ok["status"] == "success"
    assert "3" in ok["detail"]
    failed = _get_cell(body, "analyze", dt.date(2026, 8, 3))
    assert failed["status"] == "failed"
    assert failed["message"] == "claude timeout"


def test_blog_cell_from_file_presence(client) -> None:
    tc, _, blog_dir = client
    (blog_dir / "2026-08-04.txt").write_text("원고", encoding="utf-8")

    body = tc.get("/api/v1/batch-monitor?days=3").json()

    assert _get_cell(body, "blog", dt.date(2026, 8, 4))["status"] == "success"
    assert _get_cell(body, "blog", dt.date(2026, 8, 3))["status"] == "missed"


def test_stock_report_cells_from_stock_report_runs(client) -> None:
    """stock_report_runs의 created_at(UTC)이 KST 날짜로 정확히 매핑돼야 한다."""
    tc, factory, _ = client
    db = factory()
    # KST 8/4 18:30 실행 = UTC 8/4 09:30
    db.add(
        StockReportRun(
            report_type="premium", status="completed",
            created_at=_utc(dt.datetime(2026, 8, 4, 18, 30)),
        )
    )
    db.add(
        StockReportRun(
            report_type="supply", status="failed", error_message="수급 데이터 미설정",
            created_at=_utc(dt.datetime(2026, 8, 3, 18, 30)),
        )
    )
    db.add(
        StockReportRun(
            report_type="premium", status="running",
            created_at=_utc(dt.datetime(2026, 8, 5, 18, 30)),
        )
    )
    db.commit()
    db.close()

    body = tc.get("/api/v1/batch-monitor?days=3").json()

    assert _get_cell(body, "stock_report", dt.date(2026, 8, 4))["status"] == "success"
    failed = _get_cell(body, "stock_report", dt.date(2026, 8, 3))
    assert failed["status"] == "failed"
    assert failed["message"] == "수급 데이터 미설정"
    assert _get_cell(body, "stock_report", _TODAY)["status"] == "running"


def test_broker_sync_heartbeat_and_failures(client) -> None:
    """성공은 collection_log 하트비트로, 실패는 job_run_log 행으로 판정한다."""
    tc, factory, _ = client
    db = factory()
    # 어제(8/4): 하트비트만 → success
    db.add(
        CollectionLog(
            ref_date=dt.date(2026, 8, 4), source="scheduler.broker_sync",
            status="success", items=0, created_at=_utc(dt.datetime(2026, 8, 4, 15, 0)),
        )
    )
    # 8/3: 실패 행 → failed (하트비트가 있어도 실패가 우선)
    db.add(
        CollectionLog(
            ref_date=dt.date(2026, 8, 3), source="scheduler.broker_sync",
            status="success", items=0, created_at=_utc(dt.datetime(2026, 8, 3, 15, 0)),
        )
    )
    db.add(
        JobRunLog(
            name="broker_sync", status="failed", ref_date=dt.date(2026, 8, 3),
            started_at=_utc(dt.datetime(2026, 8, 3, 15, 1)), message="KIS 500",
        )
    )
    # 오늘: 마지막 하트비트 5분 전 → 신선 → success
    db.add(
        CollectionLog(
            ref_date=_TODAY, source="scheduler.broker_sync",
            status="success", items=0, created_at=_utc(_NOW - dt.timedelta(minutes=5)),
        )
    )
    db.commit()
    db.close()

    body = tc.get("/api/v1/batch-monitor?days=3").json()

    assert _get_cell(body, "broker_sync", dt.date(2026, 8, 4))["status"] == "success"
    failed = _get_cell(body, "broker_sync", dt.date(2026, 8, 3))
    assert failed["status"] == "failed"
    assert failed["message"] == "KIS 500"
    assert _get_cell(body, "broker_sync", _TODAY)["status"] == "success"


def test_broker_sync_stale_heartbeat_today_is_failed(client) -> None:
    """오늘 하트비트가 10분 넘게 끊기면 서버/스케줄러 장애 신호 → failed."""
    tc, factory, _ = client
    db = factory()
    db.add(
        CollectionLog(
            ref_date=_TODAY, source="scheduler.broker_sync",
            status="success", items=0, created_at=_utc(_NOW - dt.timedelta(minutes=30)),
        )
    )
    db.commit()
    db.close()

    body = tc.get("/api/v1/batch-monitor?days=1").json()

    cell = _get_cell(body, "broker_sync", _TODAY)
    assert cell["status"] == "failed"
    assert "하트비트" in cell["message"]


def test_rerunnable_flags_limited_to_pipeline_jobs(client) -> None:
    """재실행 버튼 대상은 run_once가 지원하는 스케줄러 잡 6개뿐이다."""
    tc, _, _ = client

    body = tc.get("/api/v1/batch-monitor?days=1").json()

    flags = {j["name"]: j["rerunnable"] for j in body["jobs"]}
    assert flags == {
        "order_cycle": True,
        "broker_sync": True,
        "eod_cleanup": True,
        "analyze": False,
        "data_collection": True,
        "candidate_generation": True,
        "validation": True,
        "stock_report": False,
        "blog": False,
    }
