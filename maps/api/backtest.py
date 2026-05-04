"""SCR-07 백테스트 콘솔 API."""

from __future__ import annotations

import datetime
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from maps.api.deps import get_db
from maps.api.schemas import BacktestResponse, BacktestRunItem, BacktestRunRequest
from maps.backtest.engine import BacktestEngine
from maps.common.exceptions import BacktestError
from maps.common.models import MonteCarloSequenceResults, WalkForwardResults
from maps.data.ohlcv_repo import HistoricalOHLCVRepository
from maps.strategy.base import BaseStrategy
from maps.strategy.pullback_v3 import PullbackV3Strategy
from maps.strategy.pullback_v2 import PullbackV2Strategy
from maps.strategy.ath_breakout_v1 import ATHBreakoutV1Strategy
from maps.strategy.ath_breakout_v2 import ATHBreakoutV2Strategy
from maps.strategy.multi_asset_trend_v1 import MultiAssetTrendV1Strategy
from maps.strategy.donchian_v1 import DonchianV1Strategy
from maps.strategy.donchian_v2 import DonchianV2Strategy

router = APIRouter(prefix="/api/v1/backtest", tags=["SCR-07 Backtest"])

# 실제 Python 클래스가 구현된 전략 레지스트리
RUNNABLE_STRATEGIES: dict[str, type[BaseStrategy]] = {
    "pullback_v3":          PullbackV3Strategy,
    "pullback_v2":          PullbackV2Strategy,
    "ath_breakout_v1":      ATHBreakoutV1Strategy,
    "ath_breakout_v2":      ATHBreakoutV2Strategy,
    "multi_asset_trend_v1": MultiAssetTrendV1Strategy,
    "donchian_v1":          DonchianV1Strategy,
    "donchian_v2":          DonchianV2Strategy,
}


@router.get("", response_model=BacktestResponse)
def get_backtest_runs(db: Session = Depends(get_db)) -> BacktestResponse:
    """최근 백테스트 실행 목록을 반환한다 (WFA 결과 기준)."""
    wfa_rows = (
        db.query(WalkForwardResults)
        .order_by(WalkForwardResults.run_date.desc(), WalkForwardResults.id.desc())
        .limit(50)
        .all()
    )

    # 전략별 최신 MC 결과 (MDD p95 표시용)
    mc_rows = (
        db.query(MonteCarloSequenceResults)
        .order_by(MonteCarloSequenceResults.run_date.desc(), MonteCarloSequenceResults.id.desc())
        .limit(200)
        .all()
    )
    latest_mc: dict[str, MonteCarloSequenceResults] = {}
    for row in mc_rows:
        if row.strategy_id not in latest_mc:
            latest_mc[row.strategy_id] = row

    runs = [
        BacktestRunItem(
            run_id=str(row.id),
            strategy_id=row.strategy_id,
            status="done",
            progress_pct=100.0,
            net_cagr=None,
            mdd=latest_mc[row.strategy_id].mdd_p95 if row.strategy_id in latest_mc else None,
            sharpe=row.sharpe_mean,
            trade_count=None,
            started_at=row.created_at.isoformat() if row.created_at else None,
        )
        for row in wfa_rows
    ]

    return BacktestResponse(
        recent_runs=runs,
        available_strategies=list(RUNNABLE_STRATEGIES.keys()),
    )


@router.post("/run", response_model=BacktestRunItem)
def run_backtest(req: BacktestRunRequest, db: Session = Depends(get_db)) -> BacktestRunItem:
    """단일 백테스트를 실행하고 성과 지표를 반환한다.

    DB에 저장된 OHLCV 데이터를 전체 사용하여 전략 신호를 생성하고
    BacktestEngine으로 성과를 계산한다. 최대 30개 종목 집계 평균을 반환한다.
    """
    if req.strategy_id not in RUNNABLE_STRATEGIES:
        raise HTTPException(
            status_code=404,
            detail=f"'{req.strategy_id}' 전략은 아직 구현되지 않았습니다. "
                   f"사용 가능: {list(RUNNABLE_STRATEGIES.keys())}",
        )

    strategy_cls = RUNNABLE_STRATEGIES[req.strategy_id]
    strategy = strategy_cls()
    params = req.params or strategy.default_params
    ma_long = int(params.get("ma_long", 20))

    repo = HistoricalOHLCVRepository(db)
    tickers = repo.list_tickers_with_history(min_bars=ma_long + 30)

    if not tickers:
        raise HTTPException(
            status_code=400,
            detail=(
                "OHLCV 히스토리 데이터가 없습니다. "
                "데이터 수집(SCR-14) 실행 후 다시 시도해 주세요."
            ),
        )

    engine = BacktestEngine()
    results = []
    errors: list[str] = []

    for ticker in tickers[:30]:
        df = repo.to_dataframe(ticker)
        if len(df) < ma_long + 5:
            continue
        try:
            r = engine.run(strategy, params, df)
            if r.total_trades > 0:
                results.append(r)
        except BacktestError as exc:
            errors.append(f"{ticker}: {exc}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{ticker}: {exc}")

    if not results:
        detail = "유효한 백테스트 결과가 없습니다."
        if errors:
            detail += f" 오류: {errors[0]}"
        raise HTTPException(status_code=422, detail=detail)

    n = len(results)
    avg_cagr = sum(r.cagr for r in results) / n
    worst_mdd = min(r.mdd for r in results)
    avg_sharpe = sum(r.sharpe for r in results) / n
    total_trades = sum(r.total_trades for r in results)

    run_id = f"bt_{req.strategy_id}_{uuid.uuid4().hex[:8]}"

    return BacktestRunItem(
        run_id=run_id,
        strategy_id=req.strategy_id,
        status="done",
        progress_pct=100.0,
        net_cagr=avg_cagr,
        mdd=worst_mdd,
        sharpe=avg_sharpe,
        trade_count=total_trades,
        started_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
    )
