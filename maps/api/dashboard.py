"""SCR-01 Dashboard API."""

from __future__ import annotations

import datetime as dt
import math

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from maps.api.deps import get_db
from maps.api.schemas import AlertItem, DashboardResponse, StrategyContribution
from maps.common.constants import STRATEGY_GROUP_MAP
from maps.common.exceptions import BrokerAdapterError
from maps.common.models import (
    CandidateSnapshot,
    HistoricalOHLCV,
    KillSwitchLog,
    PortfolioSnapshot,
    PromotionHistory,
)
from maps.execution.broker_adapter import get_broker

router = APIRouter(prefix="/api/v1/dashboard", tags=["SCR-01 Dashboard"])


@router.get("", response_model=DashboardResponse)
def get_dashboard(db: Session = Depends(get_db)) -> DashboardResponse:
    """Return dashboard summary data with sensible defaults for a fresh DB."""
    latest_promotions = _latest_promotions(db)
    strategy_ids = sorted(set(STRATEGY_GROUP_MAP) | set(latest_promotions))

    contributions = [
        StrategyContribution(
            strategy_id=sid,
            name=sid,
            contribution_pct=0.0,
            stage=latest_promotions[sid].to_stage if sid in latest_promotions else "research",
        )
        for sid in strategy_ids
    ]

    latest_ohlcv_date = db.query(func.max(HistoricalOHLCV.date)).scalar()
    total_assets, broker_alert = _broker_total_assets(db)
    metrics = _portfolio_metrics(db, total_assets)
    live_count, mock_count = _active_strategy_counts(strategy_ids, latest_promotions)
    alerts = _dashboard_alerts(db, latest_ohlcv_date)
    if broker_alert:
        alerts.insert(0, broker_alert)
    elif metrics["history_count"] < 2:
        alerts.insert(0, AlertItem(
            level="INFO",
            message="Portfolio performance metrics will update after at least two daily account snapshots are available.",
            timestamp="",
        ))

    return DashboardResponse(
        total_assets=total_assets,
        total_assets_mom_pct=metrics["mom_pct"],
        ytd_cagr=metrics["ytd_cagr"],
        current_mdd=metrics["current_mdd"],
        sharpe_1y=metrics["sharpe_1y"],
        active_strategies=live_count + mock_count,
        live_count=live_count,
        mock_count=mock_count,
        last_updated=latest_ohlcv_date.isoformat() if latest_ohlcv_date else "데이터 없음",
        contributions=contributions,
        alerts=alerts,
    )


def _latest_promotions(db: Session) -> dict[str, PromotionHistory]:
    """전략별 마지막 성공 승격 이력을 반환한다 (passed=True 만 읽음).

    실패 평가(passed=False)가 이전 성공 단계를 덮어쓰지 않도록 한다.
    """
    rows = (
        db.query(PromotionHistory)
        .filter(PromotionHistory.passed.is_(True))
        .order_by(PromotionHistory.evaluated_at.desc(), PromotionHistory.id.desc())
        .all()
    )
    latest: dict[str, PromotionHistory] = {}
    for row in rows:
        if row.strategy_id not in latest:
            latest[row.strategy_id] = row
    return latest


def _broker_total_assets(db: Session) -> tuple[float, AlertItem | None]:
    try:
        balance = get_broker().get_account_balance()
        _upsert_portfolio_snapshot(db, _today(), balance.total_value, balance.cash, balance.positions_value)
        return balance.total_value, None
    except (BrokerAdapterError, NotImplementedError, ValueError) as exc:
        return 0.0, AlertItem(
            level="WARN",
            message=f"Broker account balance unavailable: {exc}",
            timestamp="",
        )


def _today() -> dt.date:
    return dt.date.today()


def _upsert_portfolio_snapshot(
    db: Session,
    ref_date: dt.date,
    total_assets: float,
    cash: float,
    positions_value: float,
) -> None:
    row = (
        db.query(PortfolioSnapshot)
        .filter(PortfolioSnapshot.ref_date == ref_date, PortfolioSnapshot.source == "broker")
        .first()
    )
    if row is None:
        row = PortfolioSnapshot(
            ref_date=ref_date,
            source="broker",
            total_assets=total_assets,
            cash=cash,
            positions_value=positions_value,
        )
        db.add(row)
    else:
        row.total_assets = total_assets
        row.cash = cash
        row.positions_value = positions_value
        row.updated_at = dt.datetime.now(dt.timezone.utc)
    db.commit()


def _portfolio_metrics(db: Session, current_assets: float) -> dict[str, float]:
    today = _today()
    start_1y = today - dt.timedelta(days=365)
    rows = (
        db.query(PortfolioSnapshot)
        .filter(PortfolioSnapshot.source == "broker", PortfolioSnapshot.ref_date >= start_1y)
        .order_by(PortfolioSnapshot.ref_date.asc())
        .all()
    )
    points = [(row.ref_date, float(row.total_assets)) for row in rows if row.total_assets > 0]
    if not points and current_assets > 0:
        points = [(today, current_assets)]

    return {
        "mom_pct": _period_return(points, today - dt.timedelta(days=30)),
        "ytd_cagr": _ytd_cagr(points, today),
        "current_mdd": _current_mdd(points),
        "sharpe_1y": _sharpe(points),
        "history_count": float(len(points)),
    }


def _period_return(points: list[tuple[dt.date, float]], cutoff: dt.date) -> float:
    if len(points) < 2:
        return 0.0
    current = points[-1][1]
    baseline = None
    for point_date, value in reversed(points):
        if point_date <= cutoff:
            baseline = value
            break
    if baseline is None or baseline <= 0:
        return 0.0
    return current / baseline - 1.0


def _ytd_cagr(points: list[tuple[dt.date, float]], today: dt.date) -> float:
    ytd_points = [(point_date, value) for point_date, value in points if point_date >= dt.date(today.year, 1, 1)]
    if len(ytd_points) < 2:
        return 0.0
    start_date, start_value = ytd_points[0]
    end_date, end_value = ytd_points[-1]
    days = max((end_date - start_date).days, 1)
    if start_value <= 0 or end_value <= 0:
        return 0.0
    return end_value / start_value - 1.0 if days < 30 else (end_value / start_value) ** (365 / days) - 1.0


def _current_mdd(points: list[tuple[dt.date, float]]) -> float:
    peak = 0.0
    worst = 0.0
    for _point_date, value in points:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, value / peak - 1.0)
    return worst


def _sharpe(points: list[tuple[dt.date, float]]) -> float:
    if len(points) < 3:
        return 0.0
    returns = [
        points[i][1] / points[i - 1][1] - 1.0
        for i in range(1, len(points))
        if points[i - 1][1] > 0 and points[i][1] > 0
    ]
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    variance = sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
    std = math.sqrt(variance)
    return mean / std * math.sqrt(252) if std > 0 else 0.0


def _active_strategy_counts(
    strategy_ids: list[str],
    latest_promotions: dict[str, PromotionHistory],
) -> tuple[int, int]:
    live_count = 0
    mock_count = 0
    for sid in strategy_ids:
        stage = latest_promotions[sid].to_stage if sid in latest_promotions else "research"
        if stage == "live":
            live_count += 1
        elif stage != "rejected":
            mock_count += 1
    return live_count, mock_count


def _dashboard_alerts(db: Session, latest_ohlcv_date) -> list[AlertItem]:
    kill_switch_rows = db.query(KillSwitchLog).order_by(KillSwitchLog.created_at.desc()).limit(5).all()
    if kill_switch_rows:
        return [
            AlertItem(
                level="WARN" if row.event_type == "trigger" else "INFO",
                message=f"Kill Switch [{row.event_type}] {row.strategy_id}: {row.reason}",
                timestamp=row.created_at.strftime("%H:%M") if row.created_at else "",
            )
            for row in kill_switch_rows
        ]

    alerts: list[AlertItem] = []
    ohlcv_rows = db.query(func.count(HistoricalOHLCV.id)).scalar() or 0
    candidate_rows = db.query(func.count(CandidateSnapshot.id)).scalar() or 0
    if latest_ohlcv_date:
        alerts.append(
            AlertItem(
                level="INFO",
                message=f"Historical OHLCV ready: {ohlcv_rows:,} rows as of {latest_ohlcv_date.isoformat()}",
                timestamp="",
            )
        )
    if candidate_rows:
        alerts.append(
            AlertItem(
                level="INFO",
                message=f"Candidate snapshots available: {candidate_rows:,} rows",
                timestamp="",
            )
        )
    if not alerts:
        alerts.append(
            AlertItem(
                level="INFO",
                message="No operational alerts yet. Run data collection or validation to populate dashboard metrics.",
                timestamp="",
            )
        )
    return alerts
