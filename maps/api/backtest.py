"""SCR-07 백테스트 콘솔 API."""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from maps.api.deps import get_db
from maps.api.schemas import BacktestResponse, BacktestRunItem, BacktestRunRequest
from maps.backtest.cost_model import (
    BROKER_FEE_PER_SIDE,
    SLIPPAGE_LARGE_CAP,
    SLIPPAGE_SMALL_CAP,
    TRANSACTION_TAX_SELL,
)
from maps.backtest.engine import BacktestEngine
from maps.common.exceptions import BacktestError
from maps.common.models import BacktestRunLog, HistoricalOHLCV
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

# 콘솔 실행이 집계하는 종목 수 상한 (화면 표시와 실행 루프가 같은 값을 쓴다)
MAX_TICKERS = 30

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
    """최근 콘솔 백테스트 실행 목록을 반환한다.

    과거에는 WFA 결과를 목록의 원천으로 써서 net_cagr/trade_count가 항상
    None이었다. 콘솔 실행이 backtest_run_log에 저장되므로 그 로그를 읽는다.
    (검증 잡의 WFA/MC 결과는 SCR-11/SCR-08 화면이 담당한다.)
    """
    rows = (
        db.query(BacktestRunLog)
        .order_by(BacktestRunLog.id.desc())
        .limit(50)
        .all()
    )
    runs = [
        BacktestRunItem(
            run_id=row.run_id,
            strategy_id=row.strategy_id,
            status=row.status,
            progress_pct=100.0,
            net_cagr=row.net_cagr,
            mdd=row.mdd,
            sharpe=row.sharpe,
            trade_count=row.trade_count,
            started_at=row.created_at.isoformat() if row.created_at else None,
        )
        for row in rows
    ]

    data_start, data_end = (
        db.query(func.min(HistoricalOHLCV.date), func.max(HistoricalOHLCV.date)).one()
    )
    cost_summary = (
        f"수수료 {BROKER_FEE_PER_SIDE:.3%}/편도 · 거래세 {TRANSACTION_TAX_SELL:.2%} · "
        f"슬리피지 {SLIPPAGE_LARGE_CAP:.2%}~{SLIPPAGE_SMALL_CAP:.2%}×변동성 배수"
    )

    return BacktestResponse(
        recent_runs=runs,
        available_strategies=list(RUNNABLE_STRATEGIES.keys()),
        data_start=data_start,
        data_end=data_end,
        max_tickers=MAX_TICKERS,
        cost_summary=cost_summary,
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
    min_bars = strategy.required_bars(params)

    repo = HistoricalOHLCVRepository(db)
    tickers = repo.list_tickers_with_history(min_bars=min_bars)

    if not tickers:
        raise HTTPException(
            status_code=400,
            detail=(
                f"OHLCV 히스토리 데이터가 부족합니다. "
                f"{req.strategy_id} 전략은 최소 {min_bars}개 봉이 필요합니다. "
                "데이터 수집(SCR-14) 또는 OHLCV 백필을 실행한 뒤 다시 시도해 주세요."
            ),
        )

    engine = BacktestEngine()
    results = []
    errors: list[str] = []

    for ticker in tickers[:MAX_TICKERS]:
        df = repo.to_dataframe(ticker)
        if len(df) < min_bars:
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

    log = BacktestRunLog(
        run_id=run_id,
        strategy_id=req.strategy_id,
        params_json=json.dumps(req.params, ensure_ascii=False) if req.params else None,
        status="done",
        net_cagr=avg_cagr,
        mdd=worst_mdd,
        sharpe=avg_sharpe,
        trade_count=total_trades,
        ticker_count=n,
    )
    db.add(log)
    db.commit()

    return BacktestRunItem(
        run_id=run_id,
        strategy_id=req.strategy_id,
        status="done",
        progress_pct=100.0,
        net_cagr=avg_cagr,
        mdd=worst_mdd,
        sharpe=avg_sharpe,
        trade_count=total_trades,
        started_at=log.created_at.isoformat() if log.created_at else None,
    )
