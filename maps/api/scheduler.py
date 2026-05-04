"""Operational scheduler control API."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, HTTPException, Query

from maps.ops.scheduler import get_operational_scheduler

router = APIRouter(prefix="/api/v1/ops/scheduler", tags=["Ops Scheduler"])


@router.get("")
def get_scheduler_status() -> dict:
    return get_operational_scheduler().status()


@router.post("/run/{job_name}")
def run_scheduler_job(job_name: str) -> dict:
    try:
        run = get_operational_scheduler().run_once(job_name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return get_operational_scheduler()._serialize_run(run)


@router.post("/backfill/ohlcv")
def backfill_ohlcv(
    days: int = Query(default=90, ge=1, le=756),
    start: str = Query(default=""),
    end: str = Query(default=""),
) -> dict:
    try:
        end_date = dt.date.fromisoformat(end) if end else dt.date.today()
        start_date = dt.date.fromisoformat(start) if start else end_date - dt.timedelta(days=days)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="start/end must be YYYY-MM-DD") from exc
    if start_date > end_date:
        raise HTTPException(status_code=400, detail="start must be on or before end")

    run = get_operational_scheduler().backfill_ohlcv(start_date, end_date)
    return get_operational_scheduler()._serialize_run(run)
