"""다음 거래일 예정 주문 미리보기 — 브로커 호출 없이 DB+settings 기반 시뮬레이션."""

from __future__ import annotations

import datetime as dt
import logging
import math

from sqlalchemy.orm import Session

from maps.api.schemas import OrderPreviewResponse, PreviewOrderItem
from maps.common.models import CandidateSnapshot, HistoricalOHLCV, PortfolioSnapshot, PromotionHistory
from maps.common.settings import MapsSettings
from maps.ops.scheduler import _is_krx_market_day

logger = logging.getLogger(__name__)

_MAX_ORDERS = 3
_ELIGIBLE_STAGES = {"mock_candidate", "live_candidate", "live"}
_LOOKAHEAD_DAYS = 14  # 최대 탐색 일수 (긴 연휴 대비)


# ── 핵심 계산 함수 ────────────────────────────────────────────────────────────


def next_trading_day(from_date: dt.date) -> dt.date:
    """from_date 다음 날부터 KRX 거래일인 최초 날짜를 반환한다."""
    candidate = from_date + dt.timedelta(days=1)
    for _ in range(_LOOKAHEAD_DAYS):
        if _is_krx_market_day(candidate):
            return candidate
        candidate += dt.timedelta(days=1)
    return candidate


def _latest_promotions(db: Session) -> dict[str, str]:
    """전략별 최신 단계 반환 (passed=True 레코드만)."""
    rows = (
        db.query(PromotionHistory)
        .filter(PromotionHistory.passed.is_(True))
        .order_by(PromotionHistory.evaluated_at.desc(), PromotionHistory.id.desc())
        .all()
    )
    latest: dict[str, str] = {}
    for row in rows:
        if row.strategy_id not in latest:
            latest[row.strategy_id] = row.to_stage
    return latest


def _get_order_candidates(db: Session) -> list[CandidateSnapshot]:
    """주문 가능 전략의 최신 후보 종목을 final_score 내림차순으로 반환한다."""
    latest_date = (
        db.query(CandidateSnapshot.ref_date)
        .order_by(CandidateSnapshot.ref_date.desc())
        .limit(1)
        .scalar()
    )
    if latest_date is None:
        return []

    promotions = _latest_promotions(db)
    rows = (
        db.query(CandidateSnapshot)
        .filter(CandidateSnapshot.ref_date == latest_date)
        .filter(CandidateSnapshot.weekly_pass.is_(True))
        .order_by(CandidateSnapshot.final_score.desc(), CandidateSnapshot.trend_strength.desc())
        .all()
    )
    return [r for r in rows if promotions.get(r.strategy_id) in _ELIGIBLE_STAGES]


def _latest_close(db: Session, ticker: str, ref_date: dt.date) -> float:
    """ref_date 이하 가장 최근 종가를 반환한다."""
    row = (
        db.query(HistoricalOHLCV)
        .filter(HistoricalOHLCV.ticker == ticker, HistoricalOHLCV.date <= ref_date)
        .order_by(HistoricalOHLCV.date.desc())
        .first()
    )
    return float(row.close) if row and row.close and row.close > 0 else 0.0


def _estimated_qty(
    total_value: float,
    cash: float,
    limit_price: float,
    remaining_slots: int,
    max_single_exposure: float,
) -> int:
    if limit_price <= 0:
        return 0
    max_pos_value = total_value * max_single_exposure
    cash_budget = cash / max(remaining_slots, 1)
    budget = min(max_pos_value, cash_budget)
    return int(budget // limit_price)


def _get_assumed_balance(db: Session) -> tuple[float, float]:
    """portfolio_snapshot 최신값 또는 기본 fallback으로 총자산·현금을 반환한다."""
    row = (
        db.query(PortfolioSnapshot)
        .filter(PortfolioSnapshot.source == "broker")
        .order_by(PortfolioSnapshot.ref_date.desc())
        .first()
    )
    if row and row.total_assets and row.total_assets > 0:
        return float(row.total_assets), float(row.cash)
    return 100_000_000.0, 100_000_000.0  # 기본 1억원


# ── 최상위 조립 함수 ──────────────────────────────────────────────────────────


def build_order_preview(db: Session, settings: MapsSettings) -> OrderPreviewResponse:
    """다음 거래일 예정 주문 미리보기를 계산한다."""
    today = dt.date.today()
    next_day = next_trading_day(today)

    candidates = _get_order_candidates(db)
    data_available = len(candidates) > 0

    ref_date = candidates[0].ref_date if candidates else today

    # 계좌 잔고 추정
    total_value, cash = _get_assumed_balance(db)
    slippage = settings.maps_order_slippage_pct
    max_gap = settings.maps_order_max_gap_pct
    max_exposure = settings.max_single_exposure

    # 주문 가능 전략 목록
    promotions = _latest_promotions(db)
    eligible_strategies = sorted(
        {s for s, stage in promotions.items() if stage in _ELIGIBLE_STAGES}
    )

    items: list[PreviewOrderItem] = []
    submitted = 0
    seen_tickers: set[str] = set()
    remaining_cash = cash

    for candidate in candidates:
        if submitted >= _MAX_ORDERS:
            break
        if candidate.ticker in seen_tickers:
            continue

        signal_close = _latest_close(db, candidate.ticker, ref_date)
        if signal_close <= 0:
            continue

        current_close = _latest_close(db, candidate.ticker, today)
        if current_close <= 0:
            current_close = signal_close

        gap_pct = (current_close - signal_close) / signal_close
        gap_exceeded = gap_pct > max_gap

        limit_price = int(current_close * (1 + slippage))
        remaining_slots = max(_MAX_ORDERS - submitted, 1)

        if gap_exceeded:
            items.append(PreviewOrderItem(
                ticker=candidate.ticker,
                name=candidate.name,
                strategy_id=candidate.strategy_id,
                signal_date=ref_date.isoformat(),
                signal_close=signal_close,
                current_close=current_close,
                gap_pct=round(gap_pct, 4),
                gap_exceeded=True,
                limit_price=limit_price,
                estimated_qty=0,
                estimated_amount=0,
                skipped=True,
                skip_reason="gap_exceeded",
            ))
            continue

        qty = _estimated_qty(total_value, remaining_cash, limit_price, remaining_slots, max_exposure)
        if qty <= 0:
            items.append(PreviewOrderItem(
                ticker=candidate.ticker,
                name=candidate.name,
                strategy_id=candidate.strategy_id,
                signal_date=ref_date.isoformat(),
                signal_close=signal_close,
                current_close=current_close,
                gap_pct=round(gap_pct, 4),
                gap_exceeded=False,
                limit_price=limit_price,
                estimated_qty=0,
                estimated_amount=0,
                skipped=True,
                skip_reason="insufficient_cash",
            ))
            continue

        amount = limit_price * qty
        items.append(PreviewOrderItem(
            ticker=candidate.ticker,
            name=candidate.name,
            strategy_id=candidate.strategy_id,
            signal_date=ref_date.isoformat(),
            signal_close=signal_close,
            current_close=current_close,
            gap_pct=round(gap_pct, 4),
            gap_exceeded=False,
            limit_price=limit_price,
            estimated_qty=qty,
            estimated_amount=amount,
            skipped=False,
            skip_reason=None,
        ))
        seen_tickers.add(candidate.ticker)
        submitted += 1
        remaining_cash = max(remaining_cash - amount, 0.0)

    return OrderPreviewResponse(
        next_trading_day=next_day.isoformat(),
        as_of_date=ref_date.isoformat(),
        assumed_total_value=total_value,
        assumed_cash=cash,
        max_orders=_MAX_ORDERS,
        slippage_pct=slippage,
        max_gap_pct=max_gap,
        items=items,
        eligible_strategies=eligible_strategies,
        data_available=data_available,
    )
