"""SCR-21 배치 모니터 API.

일일 배치(스케줄러 잡 7개 + 외부 cron 2개)가 날짜별로 정상 실행됐는지의
매트릭스를 반환한다. 증거 원천은 잡마다 다르다:

- 파이프라인 잡 6개 → `job_run_log` (실패 포함, 재시작에도 생존)
- broker_sync 성공 → `collection_log` source='scheduler.broker_sync' 하트비트
  (실패는 job_run_log에 남는다)
- /analyze cron → `analysis_run`
- /blog cron → `{maps_blog_dir}/{date}.txt|.md` 파일 존재
- stock_report → `stock_report_runs`

상태 우선순위: skipped(비거래일) → 실행 증거 → running → pending(예정 전) → missed.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from maps.api.deps import get_db
from maps.api.schemas import BatchJobCell, BatchJobRow, BatchMonitorResponse
from maps.common.models import AnalysisRun, CollectionLog, JobRunLog, StockReportRun
from maps.common.settings import get_settings
from maps.ops.scheduler import _is_krx_market_day

router = APIRouter(prefix="/api/v1/batch-monitor", tags=["SCR-21 Batch Monitor"])

_KST_UTC_OFFSET = dt.timedelta(hours=9)

# (name, label, 시각 소스, grace분, rerunnable, kind)
# 시각 소스: settings 속성명 또는 고정 "HH:MM".
# ponytail: analyze/blog 시각은 crontab 소유라 settings 밖 — 하드코딩 상수.
_JOBS: list[tuple[str, str, str, int, bool, str]] = [
    ("order_cycle", "주문 사이클", "maps_order_time", 30, True, "pipeline"),
    ("broker_sync", "브로커 동기화", "09:00", 15, True, "broker_sync"),
    ("eod_cleanup", "EOD 정리", "maps_eod_time", 30, True, "pipeline"),
    ("analyze", "분석 픽 (/analyze)", "16:00", 90, False, "analyze"),
    ("data_collection", "데이터 수집", "maps_data_collection_time", 30, True, "pipeline"),
    ("candidate_generation", "후보 생성", "maps_candidate_time", 30, True, "pipeline"),
    ("validation", "검증", "maps_validation_time", 60, True, "pipeline"),
    ("stock_report", "종목 리포트", "maps_stock_report_time", 60, False, "stock_report"),
    ("blog", "블로그 원고 (/blog)", "18:30", 60, False, "blog"),
]

# broker_sync 하트비트가 이보다 오래되면 당일 기준 '끊김'으로 본다
_HEARTBEAT_STALE_MIN = 10


def _now() -> dt.datetime:
    """서버 로컬(KST) 현재 시각. 테스트 monkeypatch 지점."""
    return dt.datetime.now()


def _resolve_time(source: str) -> dt.time:
    """시각 소스("HH:MM" 또는 settings 속성명)를 time으로 변환한다."""
    value = source if ":" in source else getattr(get_settings(), source)
    hour, minute = value.split(":", 1)
    return dt.time(int(hour), int(minute))


def _utc_day_range(d: dt.date) -> tuple[dt.datetime, dt.datetime]:
    """KST 달력일 d에 해당하는 naive-UTC datetime 구간 [start, end)."""
    start = dt.datetime.combine(d, dt.time.min) - _KST_UTC_OFFSET
    return start, start + dt.timedelta(days=1)


def _kst_date(utc_naive: dt.datetime) -> dt.date:
    """naive-UTC datetime(모델 created_at 관례)을 KST 달력일로 변환한다."""
    return (utc_naive + _KST_UTC_OFFSET).date()


def _duration_sec(started: dt.datetime | None, finished: dt.datetime | None) -> float | None:
    """시작/종료 시각에서 소요 초를 계산한다."""
    if started is None or finished is None:
        return None
    return round((finished - started).total_seconds(), 1)


def _late_status(d: dt.date, today: dt.date, now: dt.datetime, due: dt.time, grace_min: int) -> str:
    """증거가 없을 때 pending(아직 예정 전)인지 missed인지 판정한다."""
    if d < today:
        return "missed"
    deadline = dt.datetime.combine(d, due) + dt.timedelta(minutes=grace_min)
    return "pending" if now < deadline else "missed"


@router.get("", response_model=BatchMonitorResponse)
def get_batch_monitor(
    days: int = Query(default=14, ge=1, le=30),
    db: Session = Depends(get_db),
) -> BatchMonitorResponse:
    """최근 N일의 잡별 실행 상태 매트릭스를 반환한다."""
    now = _now()
    today = now.date()
    dates = [today - dt.timedelta(days=i) for i in range(days)]
    start_date = dates[-1]
    utc_window_start, _ = _utc_day_range(start_date)

    # 벌크 조회 4개 — 날짜별 도출은 전부 메모리에서
    latest_run: dict[tuple[str, dt.date], JobRunLog] = {}
    sync_failures: dict[dt.date, list[JobRunLog]] = {}
    for row in (
        db.query(JobRunLog)
        .filter(JobRunLog.ref_date >= start_date)
        .order_by(JobRunLog.id)
        .all()
    ):
        if row.name == "broker_sync":
            sync_failures.setdefault(row.ref_date, []).append(row)
        else:
            latest_run[(row.name, row.ref_date)] = row

    heartbeats: dict[dt.date, tuple[int, dt.datetime]] = {
        ref_date: (count, last_at)
        for ref_date, count, last_at in (
            db.query(
                CollectionLog.ref_date,
                func.count(CollectionLog.id),
                func.max(CollectionLog.created_at),
            )
            .filter(
                CollectionLog.source == "scheduler.broker_sync",
                CollectionLog.ref_date >= start_date,
            )
            .group_by(CollectionLog.ref_date)
            .all()
        )
    }

    analysis_runs: dict[dt.date, AnalysisRun] = {}
    for row in (
        db.query(AnalysisRun)
        .filter(AnalysisRun.ref_date >= start_date)
        .order_by(AnalysisRun.id)
        .all()
    ):
        analysis_runs[row.ref_date] = row

    report_runs: dict[dt.date, list[StockReportRun]] = {}
    for row in (
        db.query(StockReportRun)
        .filter(StockReportRun.created_at >= utc_window_start)
        .order_by(StockReportRun.id)
        .all()
    ):
        report_runs.setdefault(_kst_date(row.created_at), []).append(row)

    blog_dir = Path(get_settings().maps_blog_dir)

    jobs: list[BatchJobRow] = []
    for name, label, time_source, grace_min, rerunnable, kind in _JOBS:
        due = _resolve_time(time_source)
        cells = [
            _build_cell(
                kind=kind,
                name=name,
                d=d,
                today=today,
                now=now,
                due=due,
                grace_min=grace_min,
                latest_run=latest_run,
                sync_failures=sync_failures,
                heartbeats=heartbeats,
                analysis_runs=analysis_runs,
                report_runs=report_runs,
                blog_dir=blog_dir,
            )
            for d in dates
        ]
        if kind == "broker_sync":
            interval = get_settings().maps_broker_sync_interval_seconds
            schedule = f"{interval}초 간격"
        elif ":" in time_source:
            schedule = f"cron {time_source}" if kind in {"analyze", "blog"} else time_source
        else:
            schedule = getattr(get_settings(), time_source)
        jobs.append(
            BatchJobRow(name=name, label=label, schedule=schedule, rerunnable=rerunnable, cells=cells)
        )

    return BatchMonitorResponse(
        days=[d.isoformat() for d in dates],
        jobs=jobs,
        generated_at=now.isoformat(),
    )


def _build_cell(
    *,
    kind: str,
    name: str,
    d: dt.date,
    today: dt.date,
    now: dt.datetime,
    due: dt.time,
    grace_min: int,
    latest_run: dict[tuple[str, dt.date], JobRunLog],
    sync_failures: dict[dt.date, list[JobRunLog]],
    heartbeats: dict[dt.date, tuple[int, dt.datetime]],
    analysis_runs: dict[dt.date, AnalysisRun],
    report_runs: dict[dt.date, list[StockReportRun]],
    blog_dir: Path,
) -> BatchJobCell:
    """잡 종류별로 날짜 셀 하나의 상태를 도출한다.

    실행 증거가 있으면 비거래일이라도 그대로 보여준다(주말 수동 실행 등).
    증거가 없을 때만 skipped(비거래일) → pending/missed 순으로 판정한다.
    """
    cell = BatchJobCell(date=d.isoformat(), status="missed")

    if kind == "pipeline":
        run = latest_run.get((name, d))
        if run is not None:
            cell.status = run.status
            cell.started_at = run.started_at.isoformat()
            cell.duration_sec = _duration_sec(run.started_at, run.finished_at)
            if run.status == "failed":
                cell.message = run.message
            return cell

    elif kind == "broker_sync":
        failures = sync_failures.get(d, [])
        heartbeat = heartbeats.get(d)
        if failures:
            cell.status = "failed"
            cell.detail = f"실패 {len(failures)}회"
            cell.message = failures[-1].message
            return cell
        if heartbeat is not None:
            count, last_at = heartbeat
            last_kst = last_at + _KST_UTC_OFFSET
            stale_min = (now - last_kst).total_seconds() / 60
            if d == today and stale_min > _HEARTBEAT_STALE_MIN:
                cell.status = "failed"
                cell.message = f"하트비트 끊김 — 마지막 {last_kst.strftime('%H:%M')}"
            else:
                cell.status = "success"
                cell.detail = f"{count}회 · 마지막 {last_kst.strftime('%H:%M')}"
            return cell

    elif kind == "analyze":
        run = analysis_runs.get(d)
        if run is not None:
            cell.status = "success" if run.status == "completed" else "failed"
            if run.status == "completed":
                cell.detail = f"픽 {run.picks_count}건"
            else:
                cell.message = run.error_message
            return cell

    elif kind == "blog":
        for suffix in (".txt", ".md"):  # 신규 .txt, 2026-07 이전 .md (maps/api/blog.py 규약)
            path = blog_dir / f"{d.isoformat()}{suffix}"
            if path.exists():
                cell.status = "success"
                cell.detail = f"{path.stat().st_size / 1024:.1f}KB"
                return cell

    elif kind == "stock_report":
        runs = report_runs.get(d, [])
        if runs:
            statuses = {r.status for r in runs}
            if "running" in statuses:
                cell.status = "running"
            elif "failed" in statuses:
                cell.status = "failed"
                cell.message = next(
                    (r.error_message for r in reversed(runs) if r.status == "failed"), None
                )
            else:
                cell.status = "success"
                cell.detail = f"{len(runs)}건 완료"
            return cell

    # 증거 없음 — stock_report만 주말에도 돈다, 나머지는 비거래일 skipped
    if kind != "stock_report" and not _is_krx_market_day(d):
        cell.status = "skipped"
        return cell
    cell.status = _late_status(d, today, now, due, grace_min)
    return cell
