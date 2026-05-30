"""Stock Report API — 리포트 생성 실행 및 결과 조회."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from maps.api.deps import get_db
from maps.api.schemas import StockReportRunItem, StockReportRunsResponse
from maps.common.db import SessionLocal
from maps.common.models import StockReportRun
from maps.stock_report.runner import REPORT_TYPES, run_all_reports, run_report

router = APIRouter(prefix="/api/v1/stock-report", tags=["Stock Report"])


def _to_item(r: StockReportRun) -> StockReportRunItem:
    return StockReportRunItem(
        id=r.id,
        report_type=r.report_type,
        report_name=REPORT_TYPES.get(r.report_type, r.report_type),
        status=r.status,
        trade_date=r.trade_date or "",
        has_html=bool(r.html_content),
        error_message=r.error_message,
        created_at=r.created_at.isoformat() if r.created_at else "",
        completed_at=r.completed_at.isoformat() if r.completed_at else None,
    )


def _run_in_background(report_type: str | None) -> None:
    """BackgroundTask 내에서 별도 DB 세션으로 리포트를 생성한다."""
    db = SessionLocal()
    try:
        if report_type:
            run_report(db, report_type)
        else:
            run_all_reports(db)
    finally:
        db.close()


@router.post("/run")
def trigger_run(
    background_tasks: BackgroundTasks,
    report_type: str | None = None,
    db: Session = Depends(get_db),
) -> dict:
    """리포트 생성을 백그라운드로 시작한다.

    report_type 미지정 시 4종 전체 실행.
    report_type: premium | updown | summary | supply
    """
    if report_type and report_type not in REPORT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"지원하지 않는 report_type: {report_type}. 가능: {list(REPORT_TYPES)}",
        )
    # 이미 실행 중인 작업이 있는지 확인
    running = (
        db.query(StockReportRun)
        .filter(StockReportRun.status == "running")
        .filter(StockReportRun.report_type == report_type if report_type else StockReportRun.report_type.isnot(None))
        .first()
    )
    if running:
        return {"status": "already_running", "run_id": running.id}

    background_tasks.add_task(_run_in_background, report_type)
    return {
        "status": "started",
        "report_type": report_type or "all",
        "message": f"{'전체' if not report_type else REPORT_TYPES.get(report_type, report_type)} 리포트 생성을 시작했습니다.",
    }


@router.get("/runs", response_model=StockReportRunsResponse)
def get_runs(
    limit: int = 20,
    db: Session = Depends(get_db),
) -> StockReportRunsResponse:
    """최근 리포트 실행 목록을 반환한다."""
    rows = (
        db.query(StockReportRun)
        .order_by(StockReportRun.created_at.desc())
        .limit(limit)
        .all()
    )
    running_count = sum(1 for r in rows if r.status == "running")
    return StockReportRunsResponse(
        runs=[_to_item(r) for r in rows],
        running_count=running_count,
        total=len(rows),
    )


@router.get("/runs/{run_id}/html")
def get_run_html(run_id: int, db: Session = Depends(get_db)) -> dict:
    """지정된 run의 HTML 리포트를 반환한다."""
    row = db.query(StockReportRun).filter(StockReportRun.id == run_id).first()
    if not row:
        raise HTTPException(status_code=404, detail=f"Run {run_id} 없음")
    if row.status != "completed":
        raise HTTPException(status_code=400, detail=f"리포트 미완료 (status={row.status})")
    if not row.html_content:
        raise HTTPException(status_code=404, detail="HTML 내용 없음")
    return {"run_id": run_id, "html": row.html_content}


@router.get("/runs/{run_id}/status")
def get_run_status(run_id: int, db: Session = Depends(get_db)) -> StockReportRunItem:
    """지정된 run의 상태를 반환한다."""
    row = db.query(StockReportRun).filter(StockReportRun.id == run_id).first()
    if not row:
        raise HTTPException(status_code=404, detail=f"Run {run_id} 없음")
    return _to_item(row)
