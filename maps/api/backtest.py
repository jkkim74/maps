"""SCR-07 백테스트 콘솔 API.

기간(start/end)·대상(universe)·실행 방식(mode)을 지정해 전략을 점검하고,
승격 게이트 상수 기반의 **1차 판정**(PASS/FAIL + 근거)을 돌려준다.
WFA/Plateau/MC 정식 검증의 대체가 아니다 — 그것은 검증 파이프라인 몫이다.

유니버스는 "풀 선정 → 기간 내 봉 수 충족 → 거래대금 상위 30" 파이프라인
하나로 통일한다. 지수 구성종목·기간 내 거래대금 모두 **현재 DB/현재 구성**
기준이라 생존자 편향이 일부 남는다(1차 판정 용도로 허용).
"""

from __future__ import annotations

import datetime as dt
import json
import statistics
import uuid
from collections import Counter
from typing import Literal

import pandas as pd
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
from maps.backtest.engine import BacktestEngine, BacktestResult
from maps.backtest.portfolio_replay import PortfolioConfig, PortfolioReplayEngine
from maps.common.constants import ALLOWED_MDD, PROMOTION_GATES, STRATEGY_GROUP_MAP
from maps.common.exceptions import BacktestError
from maps.common.models import BacktestRunLog, HistoricalOHLCV, SecurityMetadata
from maps.data.krx_auth import ensure_krx_login_guard
from maps.data.ohlcv_repo import HistoricalOHLCVRepository
from maps.strategy.base import BaseStrategy
from maps.strategy.pullback_v3 import PullbackV3Strategy
from maps.strategy.pullback_v3_3 import PullbackV33Strategy
from maps.strategy.pullback_v2 import PullbackV2Strategy
from maps.strategy.ath_breakout_v1 import ATHBreakoutV1Strategy
from maps.strategy.ath_breakout_v2 import ATHBreakoutV2Strategy
from maps.strategy.multi_asset_trend_v1 import MultiAssetTrendV1Strategy
from maps.strategy.donchian_v1 import DonchianV1Strategy
from maps.strategy.donchian_v2 import DonchianV2Strategy

router = APIRouter(prefix="/api/v1/backtest", tags=["SCR-07 Backtest"])

# 콘솔 실행이 집계하는 종목 수 상한 (화면 표시와 실행 루프가 같은 값을 쓴다)
MAX_TICKERS = 30

# 1차 판정에 필요한 최소 거래 표본 수 — 이보다 적으면 지표가 우연일 수 있다
MIN_TRADES_FOR_VERDICT = 30

# 실제 Python 클래스가 구현된 전략 레지스트리
RUNNABLE_STRATEGIES: dict[str, type[BaseStrategy]] = {
    "pullback_v3":          PullbackV3Strategy,
    "pullback_v3_3":        PullbackV33Strategy,
    "pullback_v2":          PullbackV2Strategy,
    "ath_breakout_v1":      ATHBreakoutV1Strategy,
    "ath_breakout_v2":      ATHBreakoutV2Strategy,
    "multi_asset_trend_v1": MultiAssetTrendV1Strategy,
    "donchian_v1":          DonchianV1Strategy,
    "donchian_v2":          DonchianV2Strategy,
}

# pykrx 지수 코드 — 구성종목 조회용 (현재 구성 기준)
_INDEX_CODES = {"kospi200": "1028", "kosdaq150": "2203"}
# 지수 구성 일 단위 캐시 {index_key: (조회일, tickers)} — 구성 변경은 드묾
_INDEX_CACHE: dict[str, tuple[dt.date, list[str]]] = {}


def _index_constituents(index_key: str) -> list[str]:
    """pykrx로 지수 구성종목을 조회한다 (일 단위 캐시, 로그인 가드 필수)."""
    today = dt.date.today()
    cached = _INDEX_CACHE.get(index_key)
    if cached and cached[0] == today:
        return cached[1]
    ensure_krx_login_guard()
    try:
        from pykrx import stock as _pykrx_stock

        tickers = [str(t) for t in _pykrx_stock.get_index_portfolio_deposit_file(_INDEX_CODES[index_key])]
    except Exception as exc:  # noqa: BLE001 - 외부 API 실패는 400으로 안내
        raise HTTPException(
            status_code=400, detail=f"지수 구성종목 조회 실패({index_key}): {exc}"
        ) from exc
    if not tickers:
        raise HTTPException(status_code=400, detail=f"지수 구성종목이 비어 있습니다: {index_key}")
    _INDEX_CACHE[index_key] = (today, tickers)
    return tickers


def _resolve_universe_pool(db: Session, req: BacktestRunRequest) -> tuple[list[str] | None, str]:
    """universe 요청을 (후보 풀, 저장용 라벨)로 변환한다. None 풀 = 전체."""
    universe = req.universe
    arg = (req.universe_arg or "").strip()

    if universe == "all":
        return None, "all"

    if universe == "custom":
        tickers = [t.strip() for t in (req.tickers or []) if t.strip()]
        tickers = list(dict.fromkeys(tickers))  # 순서 보존 dedupe
        if not tickers:
            raise HTTPException(status_code=400, detail="직접 지정(custom)은 tickers가 필요합니다.")
        if len(tickers) > MAX_TICKERS:
            raise HTTPException(
                status_code=400, detail=f"직접 지정 종목은 최대 {MAX_TICKERS}개입니다."
            )
        return tickers, f"custom:{len(tickers)}"

    if universe == "index":
        if arg not in _INDEX_CODES:
            raise HTTPException(
                status_code=400, detail=f"index는 {sorted(_INDEX_CODES)} 중 하나여야 합니다."
            )
        return _index_constituents(arg), f"index:{arg}"

    query = db.query(SecurityMetadata.ticker).filter(SecurityMetadata.security_type == "STOCK")
    if universe == "market":
        market = arg.upper()
        if market not in {"KOSPI", "KOSDAQ"}:
            raise HTTPException(status_code=400, detail="market은 KOSPI 또는 KOSDAQ이어야 합니다.")
        query = query.filter(SecurityMetadata.market == market)
        label = f"market:{market}"
    elif universe == "sector":
        if not arg:
            raise HTTPException(status_code=400, detail="sector는 업종명이 필요합니다.")
        query = query.filter(SecurityMetadata.sector == arg)
        label = f"sector:{arg}"
    elif universe == "theme":
        if not arg:
            raise HTTPException(status_code=400, detail="theme은 테마명이 필요합니다.")
        query = query.filter(SecurityMetadata.theme == arg)
        label = f"theme:{arg}"
    elif universe == "recent_ipo":
        try:
            days = int(arg) if arg else 90  # 기본 90일 — IPO 전략 관례
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="recent_ipo는 일수(정수)가 필요합니다.") from exc
        ref = req.end or dt.date.today()
        query = query.filter(
            SecurityMetadata.listing_date.isnot(None),
            SecurityMetadata.listing_date >= ref - dt.timedelta(days=days),
        )
        label = f"recent_ipo:{days}"
    else:
        raise HTTPException(status_code=400, detail=f"알 수 없는 universe: {universe}")

    pool = [row[0] for row in query.all()]
    if not pool:
        raise HTTPException(status_code=400, detail=f"'{label}'에 해당하는 종목이 없습니다.")
    return pool, label[:32]


def _first_pass_verdict(
    strategy_id: str,
    avg_cagr: float,
    worst_mdd: float,
    avg_sharpe: float,
    total_trades: int,
) -> tuple[str, list[dict]]:
    """백테스트 집계 지표만으로 내는 1차 판정. WFA/MC 정식 검증의 대체가 아니다."""
    group = STRATEGY_GROUP_MAP[strategy_id]
    mdd_limit = float(ALLOWED_MDD[group]["mc_p95_limit"])
    min_sharpe = float(PROMOTION_GATES["mock_candidate"]["min_sharpe"])
    criteria = [
        {"criterion": "avg_sharpe", "value": avg_sharpe, "threshold": min_sharpe,
         "passed": avg_sharpe >= min_sharpe},
        {"criterion": "worst_mdd", "value": abs(worst_mdd), "threshold": mdd_limit,
         "passed": abs(worst_mdd) <= mdd_limit},
        {"criterion": "total_trades", "value": total_trades, "threshold": MIN_TRADES_FOR_VERDICT,
         "passed": total_trades >= MIN_TRADES_FOR_VERDICT},
        {"criterion": "avg_cagr", "value": avg_cagr, "threshold": 0.0,
         "passed": avg_cagr > 0},
    ]
    verdict = "PASS" if all(c["passed"] for c in criteria) else "FAIL"
    return verdict, criteria


def _payoff_ratio(results: list[BacktestResult]) -> float | None:
    """trade_list 전체에서 손익비(평균이익/평균손실)를 계산한다."""
    wins = [t.net_pnl for r in results for t in r.trade_list if t.net_pnl > 0]
    losses = [abs(t.net_pnl) for r in results for t in r.trade_list if t.net_pnl < 0]
    if not wins or not losses:
        return None
    return round(float((sum(wins) / len(wins)) / (sum(losses) / len(losses))), 2)


def _payoff_ratio_from_trades(trades: list) -> float | None:
    wins = [float(t.net_pnl) for t in trades if t.net_pnl > 0]
    losses = [abs(float(t.net_pnl)) for t in trades if t.net_pnl < 0]
    if not wins or not losses:
        return None
    return round((sum(wins) / len(wins)) / (sum(losses) / len(losses)), 2)


def _trade_diagnostics(trades: list) -> dict:
    """상태 기반 청산의 실효를 감사할 수 있는 거래 단위 통계."""
    reasons = Counter(str(t.exit_reason) for t in trades if t.exit_reason)
    r_multiples = [float(t.r_multiple) for t in trades if t.r_multiple is not None]
    holding_days = [int(t.holding_days) for t in trades if t.holding_days is not None]
    return {
        "exit_reason_counts": dict(sorted(reasons.items())),
        "avg_r_multiple": round(sum(r_multiples) / len(r_multiples), 4) if r_multiples else None,
        "median_holding_days": (
            round(float(statistics.median(holding_days)), 1) if holding_days else None
        ),
    }


def _extended_stats_per_ticker(results: list[BacktestResult], tickers: list[str]) -> dict:
    """종목별 평균 모드의 확장 지표 — 연도별 집계는 곡선이 30개라 제외."""
    total = sum(r.total_trades for r in results)
    win_rate = (
        float(sum(r.win_rate * r.total_trades for r in results) / total) if total else 0.0
    )
    trades = [trade for result in results for trade in result.trade_list]
    return {
        "win_rate": round(win_rate, 4),
        "payoff_ratio": _payoff_ratio(results),
        "yearly_returns": None,
        "positive_month_ratio": None,
        "tickers": tickers,
        **_trade_diagnostics(trades),
    }


def _extended_stats_portfolio(result, tickers: list[str]) -> dict:
    """포트폴리오 모드의 확장 지표 — 단일 계좌 곡선이라 연/월 집계가 유효하다."""
    yearly: dict[str, float] | None = None
    positive_month_ratio: float | None = None
    if result.equity_curve and result.dates:
        series = pd.Series(
            [float(v) for v in result.equity_curve], index=pd.to_datetime(list(result.dates))
        )
        prev = float(result.initial_capital)
        yearly = {}
        for ts, value in series.resample("YE").last().dropna().items():
            yearly[str(ts.year)] = round(float(value) / prev - 1, 4)
            prev = float(value)
        monthly = series.resample("ME").last().dropna().pct_change().dropna()
        if len(monthly):
            positive_month_ratio = round(float((monthly > 0).mean()), 4)
    trades = list(getattr(result, "trade_list", []))
    return {
        "win_rate": round(float(result.win_rate), 4),
        "payoff_ratio": _payoff_ratio_from_trades(trades),
        "yearly_returns": yearly,
        "positive_month_ratio": positive_month_ratio,
        "tickers": tickers,
        **_trade_diagnostics(trades),
    }


def _run_item_from_row(row: BacktestRunLog) -> BacktestRunItem:
    """저장 행을 응답 아이템으로 변환한다. 판정·지표는 저장값 그대로(감사 기록)."""
    return BacktestRunItem(
        run_id=row.run_id,
        strategy_id=row.strategy_id,
        source=row.source,
        status=row.status,
        progress_pct=100.0,
        net_cagr=row.net_cagr,
        mdd=row.mdd,
        sharpe=row.sharpe,
        trade_count=row.trade_count,
        started_at=row.created_at.isoformat() if row.created_at else None,
        start_date=row.start_date,
        end_date=row.end_date,
        mode=row.mode,
        universe=row.universe,
        verdict=row.verdict,
        verdict_criteria=json.loads(row.verdict_json) if row.verdict_json else None,
        stats=json.loads(row.stats_json) if row.stats_json else None,
    )


@router.get("", response_model=BacktestResponse)
def get_backtest_runs(
    source: Literal["manual", "scheduled_validation"] | None = None,
    db: Session = Depends(get_db),
) -> BacktestResponse:
    """최근 콘솔 백테스트 실행 목록을 반환한다.

    과거에는 WFA 결과를 목록의 원천으로 써서 net_cagr/trade_count가 항상
    None이었다. 콘솔 실행이 backtest_run_log에 저장되므로 그 로그를 읽는다.
    (검증 잡의 WFA/MC 결과는 SCR-11/SCR-08 화면이 담당한다.)
    """
    query = db.query(BacktestRunLog)
    if source is not None:
        query = query.filter(BacktestRunLog.source == source)
    rows = (
        query.order_by(BacktestRunLog.created_at.desc(), BacktestRunLog.id.desc())
        .limit(50)
        .all()
    )
    runs = [_run_item_from_row(row) for row in rows]

    data_start, data_end = (
        db.query(func.min(HistoricalOHLCV.date), func.max(HistoricalOHLCV.date)).one()
    )
    cost_summary = (
        f"수수료 {BROKER_FEE_PER_SIDE:.3%}/편도 · 거래세 {TRANSACTION_TAX_SELL:.2%} · "
        f"슬리피지 {SLIPPAGE_LARGE_CAP:.2%}~{SLIPPAGE_SMALL_CAP:.2%}×변동성 배수"
    )

    def _distinct(column) -> list[str]:
        rows = (
            db.query(column)
            .filter(SecurityMetadata.security_type == "STOCK", column.isnot(None))
            .distinct()
            .all()
        )
        return sorted({row[0] for row in rows if row[0]})

    return BacktestResponse(
        recent_runs=runs,
        available_strategies=list(RUNNABLE_STRATEGIES.keys()),
        data_start=data_start,
        data_end=data_end,
        max_tickers=MAX_TICKERS,
        cost_summary=cost_summary,
        universe_options={
            "markets": ["KOSPI", "KOSDAQ"],
            "indices": sorted(_INDEX_CODES),
            "sectors": _distinct(SecurityMetadata.sector),
            "themes": _distinct(SecurityMetadata.theme),
        },
    )


@router.post("/run", response_model=BacktestRunItem)
def run_backtest(req: BacktestRunRequest, db: Session = Depends(get_db)) -> BacktestRunItem:
    """백테스트를 실행하고 성과 지표 + 1차 판정을 반환한다.

    - 기간: start/end 미지정 시 DB 보유 전체
    - 대상: universe 풀 → 기간 내 거래대금 상위 30 (custom은 지정 종목 그대로)
    - 방식: per_ticker(종목별 독립지갑 평균 — 전략 체질검사) |
      portfolio(PortfolioReplayEngine 슬롯 공유 자본 — 계좌 시뮬레이션)
    """
    if req.strategy_id not in RUNNABLE_STRATEGIES:
        raise HTTPException(
            status_code=404,
            detail=f"'{req.strategy_id}' 전략은 아직 구현되지 않았습니다. "
                   f"사용 가능: {list(RUNNABLE_STRATEGIES.keys())}",
        )
    if req.mode not in {"per_ticker", "portfolio"}:
        raise HTTPException(status_code=400, detail="mode는 per_ticker 또는 portfolio여야 합니다.")
    if req.start and req.end and req.start >= req.end:
        raise HTTPException(status_code=400, detail="start는 end보다 앞서야 합니다.")

    strategy_cls = RUNNABLE_STRATEGIES[req.strategy_id]
    strategy = strategy_cls()
    params = req.params or strategy.default_params
    min_bars = strategy.required_bars(params)

    pool, universe_label = _resolve_universe_pool(db, req)
    repo = HistoricalOHLCVRepository(db)
    if req.universe == "custom":
        tickers = pool or []
    else:
        tickers = repo.top_tickers_by_trading_value(
            start=req.start, end=req.end, min_bars=min_bars, limit=MAX_TICKERS, pool=pool
        )

    if not tickers:
        raise HTTPException(
            status_code=400,
            detail=(
                f"선택한 기간·대상에 OHLCV 데이터가 부족합니다. "
                f"{req.strategy_id} 전략은 최소 {min_bars}개 봉이 필요합니다. "
                "기간을 넓히거나 데이터 수집(SCR-14)/OHLCV 백필을 실행해 주세요."
            ),
        )

    frames: dict[str, pd.DataFrame] = {}
    for ticker in tickers:
        df = repo.to_dataframe(ticker, start=req.start, end=req.end)
        if len(df) >= min_bars:
            frames[ticker] = df
    if not frames:
        raise HTTPException(
            status_code=400,
            detail=f"기간 내 봉 수가 최소치({min_bars})를 넘는 종목이 없습니다.",
        )

    if req.mode == "portfolio":
        try:
            presult = PortfolioReplayEngine(PortfolioConfig()).run(strategy, params, frames)
        except BacktestError as exc:
            raise HTTPException(status_code=422, detail=f"포트폴리오 리플레이 실패: {exc}") from exc
        if presult.total_trades == 0:
            raise HTTPException(status_code=422, detail="기간 내 체결된 거래가 없습니다.")
        avg_cagr = float(presult.cagr)
        worst_mdd = float(presult.mdd)
        avg_sharpe = float(presult.sharpe)
        total_trades = int(presult.total_trades)
        ticker_count = len(frames)
        stats = _extended_stats_portfolio(presult, sorted(frames))
    else:
        engine = BacktestEngine()
        results: list[BacktestResult] = []
        used_tickers: list[str] = []
        errors: list[str] = []
        for ticker, df in frames.items():
            try:
                r = engine.run(strategy, params, df)
                if r.total_trades > 0:
                    results.append(r)
                    used_tickers.append(ticker)
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
        # float()/int() 강제: 엔진 지표는 numpy 스칼라일 수 있고, psycopg2는
        # np.float64를 적재하지 못한다 (2026-08-02 운영 INSERT 실패).
        avg_cagr = float(sum(r.cagr for r in results) / n)
        worst_mdd = float(min(r.mdd for r in results))
        avg_sharpe = float(sum(r.sharpe for r in results) / n)
        total_trades = int(sum(r.total_trades for r in results))
        ticker_count = n
        stats = _extended_stats_per_ticker(results, used_tickers)

    verdict, criteria = _first_pass_verdict(
        req.strategy_id, avg_cagr, worst_mdd, avg_sharpe, total_trades
    )

    run_id = f"bt_{req.strategy_id}_{uuid.uuid4().hex[:8]}"

    log = BacktestRunLog(
        run_id=run_id,
        strategy_id=req.strategy_id,
        source="manual",
        params_json=json.dumps(req.params, ensure_ascii=False) if req.params else None,
        status="done",
        net_cagr=avg_cagr,
        mdd=worst_mdd,
        sharpe=avg_sharpe,
        trade_count=total_trades,
        ticker_count=ticker_count,
        start_date=req.start,
        end_date=req.end,
        mode=req.mode,
        universe=universe_label,
        verdict=verdict,
        verdict_json=json.dumps(criteria, ensure_ascii=False),
        stats_json=json.dumps(stats, ensure_ascii=False),
    )
    db.add(log)
    db.commit()

    return _run_item_from_row(log)
