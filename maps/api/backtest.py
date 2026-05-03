"""SCR-07 백테스트 콘솔 API."""

from __future__ import annotations

from fastapi import APIRouter

from maps.api.schemas import BacktestResponse

router = APIRouter(prefix="/api/v1/backtest", tags=["SCR-07 Backtest"])


@router.get("", response_model=BacktestResponse)
def get_backtest_runs() -> BacktestResponse:
    """최근 백테스트 실행 목록을 반환한다.

    Phase 4 이후 BacktestEngine 비동기 실행 큐와 연동 예정.
    """
    return BacktestResponse(recent_runs=[])
