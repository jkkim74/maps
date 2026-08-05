"""SCR-06 리스크/모니터 API."""

from __future__ import annotations

import datetime
import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from maps.api.deps import get_db
from maps.api.schemas import ActiveKillItem, HoldingItem, RiskGaugeItem, RiskResponse
from maps.common.constants import ALLOWED_MDD, STRATEGY_GROUP_MAP
from maps.common.exceptions import BrokerAdapterError
from maps.backtest.engine import _compute_atr14
from maps.common.models import (
    AnalysisPick,
    HistoricalOHLCV,
    KillSwitchLog,
    MonteCarloSequenceResults,
    OrderLog,
    SecurityMetadata,
)
from maps.common.settings import get_settings
from maps.execution.broker_adapter import get_broker
from maps.strategy.live_rules import effective_stop_price

router = APIRouter(prefix="/api/v1/risk", tags=["SCR-06 Risk"])
logger = logging.getLogger(__name__)


def _atr14_for_ticker(db: Session, ticker: str) -> float | None:
    """DB에서 최근 OHLCV를 조회해 ATR(14) 최신값을 반환한다.

    ``order_log.atr14`` 가 없는 주문(옛 기록·외부 매수)에만 쓰는 폴백이다.

    lookback 은 **400봉**으로 청산 경로(`scheduler._latest_strategy_signal`)와
    맞춘다. Wilder 평활은 워밍업 길이에 따라 값이 달라져서, 20봉으로 재면
    화면의 손절가가 실제 청산 기준과 다른 값이 된다(2026-07-30 실측:
    400봉 1,874.4 vs 60봉 1,886.8).
    """
    import pandas as pd

    rows = (
        db.query(HistoricalOHLCV)
        .filter(HistoricalOHLCV.ticker == ticker)
        .order_by(HistoricalOHLCV.date.desc())
        .limit(400)
        .all()
    )
    if len(rows) < 14:
        return None
    frame = pd.DataFrame(
        [{"high": r.high, "low": r.low, "close": r.close} for r in reversed(rows)]
    )
    atr_series = _compute_atr14(frame)
    last = atr_series.iloc[-1] if not atr_series.empty else float("nan")
    return float(last) if pd.notna(last) else None

@router.get("", response_model=RiskResponse)
def get_risk(db: Session = Depends(get_db)) -> RiskResponse:
    """전략군별 리스크 게이지 및 Kill Switch 현황을 반환한다."""
    # 전략별 최신 Kill Switch 이벤트
    recent_kills = (
        db.query(KillSwitchLog)
        .order_by(KillSwitchLog.created_at.desc(), KillSwitchLog.id.desc())
        .limit(200)
        .all()
    )
    latest_kill: dict[str, KillSwitchLog] = {}
    for row in recent_kills:
        if row.strategy_id and row.strategy_id not in latest_kill:
            latest_kill[row.strategy_id] = row

    active_kills = [r for r in latest_kill.values() if r.event_type in ("trigger", "approved")]
    active_kill_count = len(active_kills)
    active_kill_items = [
        ActiveKillItem(
            strategy_id=r.strategy_id or "",
            reason=r.reason,
            created_at=(
                r.created_at.replace(tzinfo=datetime.timezone.utc).isoformat()
                if r.created_at and r.created_at.tzinfo is None
                else (r.created_at.isoformat() if r.created_at else "")
            ),
        )
        for r in sorted(active_kills, key=lambda r: r.strategy_id or "")
    ]

    # 전략별 최신 Monte Carlo 결과로 게이지 구성
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

    gauges = [
        RiskGaugeItem(
            strategy_id=row.strategy_id,
            current_risk=row.mdd_p95,
            limit=row.mdd_limit,
            ratio=row.mdd_p95 / row.mdd_limit if row.mdd_limit > 0 else 0.0,
        )
        for row in latest_mc.values()
    ]
    if not gauges:
        gauges = _default_strategy_gauges()

    # long_term_risk: 전략 중 가장 높은 MDD p95/limit 비율
    max_ratio = max((g.ratio for g in gauges), default=0.0)
    holdings, max_exposure_pct, broker_position_count, broker_status, broker_error = (
        _broker_holdings(db)
    )

    return RiskResponse(
        short_term_risk=0.0,        # 당일 PnL은 실시간 계좌 연동 필요 (Phase 5)
        short_term_limit=get_settings().daily_loss_limit,
        long_term_risk=max_ratio,
        long_term_limit=1.0,        # 1.0 = 한도 100% 도달
        max_exposure_pct=max_exposure_pct,
        # 보유 종목 수는 holdings 길이 그대로다. 과거에는 Kill Switch 수와
        # max()로 합쳐서 kill이 많으면 KPI가 실제 행 수보다 커졌다.
        position_count=broker_position_count,
        gauges=gauges,
        holdings=holdings,
        broker_status=broker_status,
        broker_error=broker_error,
        active_kill_count=active_kill_count,
        active_kills=active_kill_items,
    )


def _default_strategy_gauges() -> list[RiskGaugeItem]:
    gauges: list[RiskGaugeItem] = []
    for strategy_id in sorted(STRATEGY_GROUP_MAP):
        group = STRATEGY_GROUP_MAP[strategy_id]
        limit = ALLOWED_MDD.get(group, {}).get("mc_p95_limit", 0.0)
        gauges.append(
            RiskGaugeItem(
                strategy_id=strategy_id,
                current_risk=0.0,
                limit=limit,
                ratio=0.0,
            )
        )
    return gauges


def _broker_holdings(db: Session) -> tuple[list[HoldingItem], float, int, str, str | None]:
    """브로커 실시간 보유 내역을 반환한다.

    Returns:
        (holdings, max_exposure_pct, position_count, broker_status, broker_error).
        broker_status: "ok"(실시간) | "fallback"(DB 근사) | "unavailable"(둘 다 실패).
    """
    try:
        broker = get_broker()
        balance = broker.get_account_balance()
        total_value = balance.total_value
        positions = getattr(broker, "_fetch_positions_and_balance", None)
        if callable(positions):
            position_map, _balance = positions()
        else:
            position_map = {
                ticker: broker.get_position(ticker)
                for ticker, qty in broker.get_positions().items()
                if qty > 0
            }
        holdings: list[HoldingItem] = []
        tickers = set(position_map)
        name_map = {
            row.ticker: row.name
            for row in db.query(SecurityMetadata).filter(SecurityMetadata.ticker.in_(tickers)).all()
        } if tickers else {}
        strategy_map: dict[str, str] = {}
        entry_price_map: dict[str, float] = {}
        # 진입 시점 ATR. 화면이 청산 판정과 같은 손절가를 보여주려면 같은 입력을 써야 한다.
        entry_atr_map: dict[str, float | None] = {}
        if tickers:
            rows = (
                db.query(OrderLog)
                .filter(OrderLog.ticker.in_(tickers))
                .filter(OrderLog.side == "buy")
                .order_by(OrderLog.created_at.desc(), OrderLog.id.desc())
                .all()
            )
            for row in rows:
                if (
                    row.strategy_id
                    and row.status in ("filled", "partially_filled")
                    and row.ticker not in strategy_map
                ):
                    strategy_map[row.ticker] = row.strategy_id
                    entry_price_map[row.ticker] = row.fill_price or row.order_price or 0.0
                    entry_atr_map[row.ticker] = row.atr14
            for row in rows:
                position = position_map.get(row.ticker)
                if (
                    row.strategy_id
                    and row.ticker not in strategy_map
                    and position is not None
                    and row.qty == position.quantity
                    and row.status in ("pending", "partially_filled", "expired")
                ):
                    strategy_map[row.ticker] = row.strategy_id
                    entry_price_map[row.ticker] = row.fill_price or row.order_price or position.avg_price
                    entry_atr_map[row.ticker] = row.atr14
        for ticker, position in position_map.items():
            if position is None:
                continue
            market_value = position.market_value
            if position.quantity <= 0 or market_value <= 0:
                continue
            exposure = market_value / total_value if total_value > 0 else 0.0
            current_price = (
                position.current_price
                if position.current_price is not None
                else position.avg_price
            )
            strategy_id = strategy_map.get(ticker, "broker")
            entry_price = round(entry_price_map.get(ticker) or position.avg_price)
            # 진입 시점 ATR 우선. 없을 때만(옛 주문·외부 매수) 현재 시점으로 폴백한다.
            atr14 = entry_atr_map.get(ticker) or _atr14_for_ticker(db, ticker)
            raw_stop = effective_stop_price(strategy_map.get(ticker), entry_price, atr14)
            stop_price = round(raw_stop) if raw_stop is not None else None
            holdings.append(
                HoldingItem(
                    ticker=ticker,
                    name=position.name or name_map.get(ticker, ""),
                    strategy_id=strategy_id,
                    entry_price=float(entry_price),
                    current_price=current_price,
                    pnl_pct=(
                        current_price / entry_price - 1.0
                        if entry_price > 0
                        else None
                    ),
                    exposure_pct=exposure,
                    stop_price=float(stop_price) if stop_price is not None else None,
                    quantity=int(position.quantity),
                    market_value=float(market_value),
                )
            )
        max_exposure = max((item.exposure_pct for item in holdings), default=0.0)
        return holdings, max_exposure, len(holdings), "ok", None
    except (BrokerAdapterError, NotImplementedError, ValueError) as exc:
        logger.warning("Risk broker holdings unavailable: %s", exc)
        fallback = _fallback_holdings(db)
        status = "fallback" if fallback else "unavailable"
        return fallback, 0.0, len(fallback), status, str(exc)


def _fallback_holdings(db: Session) -> list[HoldingItem]:
    """브로커 조회 실패 시 DB 기록으로 보유 내역을 근사한다.

    전략매매 엔진이 추적하는 analysis_pick(state=BOUGHT)을 사용하며,
    현재가는 최신 OHLCV 종가로 대신한다. 실시간 잔고가 아니므로
    exposure_pct는 계산하지 않는다(0.0).
    """
    picks = (
        db.query(AnalysisPick)
        .filter(AnalysisPick.state == "BOUGHT")
        .order_by(AnalysisPick.updated_at.desc())
        .all()
    )
    holdings: list[HoldingItem] = []
    seen: set[str] = set()
    for pick in picks:
        if pick.ticker in seen:
            continue
        seen.add(pick.ticker)
        entry_price = pick.buy_price or 0.0
        if pick.entry_order_id:
            order = db.query(OrderLog).filter(OrderLog.order_id == pick.entry_order_id).first()
            if order is not None and (order.fill_price or order.order_price):
                entry_price = order.fill_price or order.order_price
        current_price = _latest_close(db, pick.ticker)
        holdings.append(
            HoldingItem(
                ticker=pick.ticker,
                name=pick.name,
                strategy_id=pick.strategy_context or "strategy_trade",
                entry_price=float(entry_price),
                current_price=current_price,
                pnl_pct=(
                    current_price / entry_price - 1.0
                    if current_price is not None and entry_price > 0
                    else None
                ),
                exposure_pct=0.0,
                stop_price=float(pick.stop_price) if pick.stop_price else None,
            )
        )
    return holdings


def _latest_close(db: Session, ticker: str) -> float | None:
    """최신 OHLCV 종가를 반환한다 (없으면 None)."""
    row = (
        db.query(HistoricalOHLCV)
        .filter(HistoricalOHLCV.ticker == ticker)
        .order_by(HistoricalOHLCV.date.desc())
        .first()
    )
    return float(row.close) if row is not None else None
