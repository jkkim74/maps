"""SCR-06 리스크/모니터 API."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from maps.api.deps import get_db
from maps.api.schemas import HoldingItem, RiskGaugeItem, RiskResponse
from maps.common.constants import ALLOWED_MDD, STRATEGY_GROUP_MAP
from maps.common.exceptions import BrokerAdapterError
from maps.common.models import KillSwitchLog, MonteCarloSequenceResults
from maps.common.settings import get_settings
from maps.execution.broker_adapter import get_broker

router = APIRouter(prefix="/api/v1/risk", tags=["SCR-06 Risk"])
logger = logging.getLogger(__name__)

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
    position_count = len(active_kills)

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
    holdings, max_exposure_pct, broker_position_count = _broker_holdings()

    return RiskResponse(
        short_term_risk=0.0,        # 당일 PnL은 실시간 계좌 연동 필요 (Phase 5)
        short_term_limit=get_settings().daily_loss_limit,
        long_term_risk=max_ratio,
        long_term_limit=1.0,        # 1.0 = 한도 100% 도달
        max_exposure_pct=max_exposure_pct,
        position_count=max(position_count, broker_position_count),
        gauges=gauges,
        holdings=holdings,
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


def _broker_holdings() -> tuple[list[HoldingItem], float, int]:
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
        for ticker, position in position_map.items():
            if position is None:
                continue
            market_value = position.market_value
            exposure = market_value / total_value if total_value > 0 else 0.0
            holdings.append(
                HoldingItem(
                    ticker=ticker,
                    strategy_id="broker",
                    entry_price=position.avg_price,
                    current_price=position.avg_price,
                    pnl_pct=0.0,
                    exposure_pct=exposure,
                    stop_price=None,
                )
            )
        max_exposure = max((item.exposure_pct for item in holdings), default=0.0)
        return holdings, max_exposure, len(holdings)
    except (BrokerAdapterError, NotImplementedError, ValueError) as exc:
        logger.warning("Risk broker holdings unavailable: %s", exc)
        return [], 0.0, 0
