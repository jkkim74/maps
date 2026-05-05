"""SCR-11 Walk-Forward Report API — P0."""

from __future__ import annotations

import datetime
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from maps.api.deps import get_db
from maps.api.schemas import FoldResultItem, WfaResponse
from maps.common.models import WalkForwardFoldResults, WalkForwardResults
from maps.data.ohlcv_repo import HistoricalOHLCVRepository
from maps.strategy.base import BaseStrategy
from maps.strategy.pullback_v3 import PullbackV3Strategy
from maps.strategy.pullback_v2 import PullbackV2Strategy
from maps.strategy.ath_breakout_v1 import ATHBreakoutV1Strategy
from maps.strategy.ath_breakout_v2 import ATHBreakoutV2Strategy
from maps.strategy.multi_asset_trend_v1 import MultiAssetTrendV1Strategy
from maps.strategy.donchian_v1 import DonchianV1Strategy
from maps.strategy.donchian_v2 import DonchianV2Strategy
from maps.validation.walk_forward import WalkForwardAnalyzer

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/wfa", tags=["SCR-11 WFA"])

RUNNABLE_STRATEGIES: dict[str, type[BaseStrategy]] = {
    "pullback_v3":          PullbackV3Strategy,
    "pullback_v2":          PullbackV2Strategy,
    "ath_breakout_v1":      ATHBreakoutV1Strategy,
    "ath_breakout_v2":      ATHBreakoutV2Strategy,
    "multi_asset_trend_v1": MultiAssetTrendV1Strategy,
    "donchian_v1":          DonchianV1Strategy,
    "donchian_v2":          DonchianV2Strategy,
}


def _build_response(strategy_id: str, row: WalkForwardResults, fold_rows: list[WalkForwardFoldResults]) -> WfaResponse:
    cv = (row.sharpe_std / abs(row.sharpe_mean)) if row.sharpe_mean != 0 else None
    fail_reasons = json.loads(row.fail_reasons_json) if row.fail_reasons_json else []

    folds = [
        FoldResultItem(
            fold_idx=f.fold_idx,
            is_start=f.is_start.isoformat(),
            is_end=f.is_end.isoformat(),
            oos_start=f.oos_start.isoformat(),
            oos_end=f.oos_end.isoformat(),
            is_sharpe=f.is_sharpe,
            oos_sharpe=f.oos_sharpe,
            is_g2p=f.is_g2p,
            oos_g2p=f.oos_g2p,
            g2p_ratio=f.g2p_ratio,
            best_params=json.loads(f.best_params_json) if f.best_params_json else None,
        )
        for f in sorted(fold_rows, key=lambda x: x.fold_idx)
    ]

    return WfaResponse(
        strategy_id=strategy_id,
        passed=row.passed,
        sharpe_mean=row.sharpe_mean,
        cv=round(cv, 3) if cv is not None else None,
        negative_folds=row.negative_folds,
        mean_g2p=row.mean_g2p,
        fail_reasons=fail_reasons,
        folds=folds,
        run_date=row.run_date.isoformat() if row.run_date else None,
    )


@router.get("", response_model=WfaResponse)
def get_wfa(
    strategy_id: str = Query(default="pullback_v3"),
    db: Session = Depends(get_db),
) -> WfaResponse:
    """Walk-Forward 결과를 반환한다."""
    row = (
        db.query(WalkForwardResults)
        .filter(WalkForwardResults.strategy_id == strategy_id)
        .order_by(WalkForwardResults.run_date.desc())
        .first()
    )

    if not row:
        return WfaResponse(
            strategy_id=strategy_id,
            passed=False,
            sharpe_mean=0.0,
            cv=0.0,
            negative_folds=0,
            mean_g2p=0.0,
            fail_reasons=["결과 없음 — WFA 실행 버튼을 눌러 분석을 시작하세요"],
            folds=[],
            run_date=datetime.date.today().isoformat(),
        )

    fold_rows = (
        db.query(WalkForwardFoldResults)
        .filter(WalkForwardFoldResults.wfa_run_id == row.id)
        .all()
    )
    return _build_response(strategy_id, row, fold_rows)


@router.post("/run", response_model=WfaResponse)
def run_wfa(
    strategy_id: str = Query(default="pullback_v3"),
    db: Session = Depends(get_db),
) -> WfaResponse:
    """WFA를 실행하고 결과를 DB에 저장한 뒤 반환한다."""
    if strategy_id not in RUNNABLE_STRATEGIES:
        raise HTTPException(
            status_code=404,
            detail=f"'{strategy_id}' 전략은 지원되지 않습니다. 사용 가능: {list(RUNNABLE_STRATEGIES.keys())}",
        )

    strategy_cls = RUNNABLE_STRATEGIES[strategy_id]
    strategy = strategy_cls()
    params = strategy.default_params
    min_bars = strategy.required_bars(params)

    repo = HistoricalOHLCVRepository(db)
    tickers = repo.list_tickers_with_history(min_bars=min_bars)

    if not tickers:
        raise HTTPException(
            status_code=400,
            detail=(
                f"OHLCV 데이터가 부족합니다. {strategy_id} 전략은 최소 {min_bars}개 봉이 필요합니다. "
                "SCR-14 데이터 품질 화면에서 데이터 수집 후 재시도하세요."
            ),
        )

    # 데이터가 가장 많은 종목으로 WFA 실행
    ticker = tickers[0]
    df = repo.to_dataframe(ticker)

    analyzer = WalkForwardAnalyzer()
    wfa_result = analyzer.run(strategy, df, strategy.param_grid())

    today = datetime.date.today()
    sharpe_mean = wfa_result.sharpe_mean
    sharpe_std = wfa_result.sharpe_std

    # 요약 행 저장 (이전 결과 교체)
    old_row = (
        db.query(WalkForwardResults)
        .filter(WalkForwardResults.strategy_id == strategy_id)
        .order_by(WalkForwardResults.run_date.desc())
        .first()
    )

    summary_row = WalkForwardResults(
        strategy_id=strategy_id,
        run_date=today,
        n_folds=len(wfa_result.folds),
        sharpe_mean=sharpe_mean,
        sharpe_std=sharpe_std,
        negative_folds=wfa_result.negative_folds,
        mean_g2p=wfa_result.mean_g2p,
        passed=wfa_result.passed,
        fail_reasons_json=json.dumps(wfa_result.fail_reasons, ensure_ascii=False) if wfa_result.fail_reasons else None,
    )
    db.add(summary_row)
    db.flush()  # summary_row.id 확보

    # 이전 fold 결과 삭제
    if old_row:
        db.query(WalkForwardFoldResults).filter(
            WalkForwardFoldResults.wfa_run_id == old_row.id
        ).delete()

    # fold 상세 저장
    for fold in wfa_result.folds:
        db.add(WalkForwardFoldResults(
            wfa_run_id=summary_row.id,
            strategy_id=strategy_id,
            fold_idx=fold.fold_idx,
            is_start=fold.is_start,
            is_end=fold.is_end,
            oos_start=fold.oos_start,
            oos_end=fold.oos_end,
            is_sharpe=fold.is_sharpe,
            oos_sharpe=fold.oos_sharpe,
            is_g2p=fold.is_g2p,
            oos_g2p=fold.oos_g2p,
            g2p_ratio=fold.g2p_ratio,
            best_params_json=None,
        ))

    db.commit()

    fold_rows = (
        db.query(WalkForwardFoldResults)
        .filter(WalkForwardFoldResults.wfa_run_id == summary_row.id)
        .all()
    )
    logger.info("WFA 완료: strategy=%s, passed=%s, folds=%d", strategy_id, wfa_result.passed, len(wfa_result.folds))
    return _build_response(strategy_id, summary_row, fold_rows)
