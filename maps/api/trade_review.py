"""SCR-17 거래 리뷰 API — 매수/매도 성과 분석."""

from __future__ import annotations

import datetime
from collections import defaultdict

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from maps.api.deps import get_db
from maps.api.schemas import (
    StrategyTradeStats,
    TradeReviewItem,
    TradeReviewResponse,
    TradeReviewSummary,
)
from maps.common.account_history import account_history_start_date, account_history_start_utc_naive
from maps.common.models import (
    HistoricalOHLCV,
    OrderLog,
    PortfolioSnapshot,
    SecurityMetadata,
)

router = APIRouter(prefix="/api/v1/trade-review", tags=["SCR-17 Trade Review"])


def _close_on_or_before(db: Session, ticker: str, ref_date: datetime.date) -> float | None:
    """ref_date 이전 가장 최근 종가를 반환한다."""
    row = (
        db.query(HistoricalOHLCV)
        .filter(HistoricalOHLCV.ticker == ticker, HistoricalOHLCV.date <= ref_date)
        .order_by(HistoricalOHLCV.date.desc())
        .first()
    )
    return float(row.close) if row and row.close else None


@router.get("", response_model=TradeReviewResponse)
def get_trade_review(db: Session = Depends(get_db)) -> TradeReviewResponse:
    """매수/매도 거래 이력을 분석하여 성과 리뷰를 반환한다."""

    # ── 기준 데이터 수집 ──────────────────────────────────────────────────
    name_map = {m.ticker: m.name for m in db.query(SecurityMetadata).all()}

    # 초기 자산: source='broker' 중 가장 오래된 스냅샷
    account_start_date = account_history_start_date()
    account_start_utc = account_history_start_utc_naive()
    snapshot_query = (
        db.query(PortfolioSnapshot)
        .filter(PortfolioSnapshot.source == "broker")
    )
    if account_start_date is not None:
        snapshot_query = snapshot_query.filter(PortfolioSnapshot.ref_date >= account_start_date)
    all_snaps = snapshot_query.order_by(PortfolioSnapshot.ref_date.asc()).all()
    initial_assets = float(all_snaps[0].total_assets) if all_snaps else 100_000_000.0
    latest_snap = all_snaps[-1] if all_snaps else None
    current_assets = float(latest_snap.total_assets) if latest_snap else initial_assets
    current_holdings: dict[str, int] = latest_snap.holdings or {} if latest_snap else {}

    today = datetime.date.today()

    # ── 체결 매수 주문 ────────────────────────────────────────────────────
    buy_query = (
        db.query(OrderLog)
        .filter(
            OrderLog.side == "buy",
            OrderLog.status.in_(["filled", "partially_filled"]),
            OrderLog.fill_price.isnot(None),
            OrderLog.fill_qty > 0,
        )
    )
    if account_start_utc is not None:
        buy_query = buy_query.filter(OrderLog.created_at >= account_start_utc)
    buy_orders = buy_query.order_by(OrderLog.created_at.asc()).all()

    # ── 매도 주문 (체결 포함, 만료 포함) ──────────────────────────────────
    sell_query = (
        db.query(OrderLog)
        .filter(OrderLog.side == "sell")
    )
    if account_start_utc is not None:
        sell_query = sell_query.filter(OrderLog.created_at >= account_start_utc)
    sell_orders = sell_query.order_by(OrderLog.created_at.asc()).all()
    # 티커별 매도 주문 매핑 (가장 최근 것이 최종 청산으로 가정)
    sell_by_ticker: dict[str, list[OrderLog]] = defaultdict(list)
    for s in sell_orders:
        sell_by_ticker[s.ticker].append(s)

    # ── 티커별 매수 집계 (여러 번 매수 시 가중평균) ────────────────────────
    # (ticker, strategy_id) 단위로 포지션 묶기
    position_key_orders: dict[tuple[str, str], list[OrderLog]] = defaultdict(list)
    for b in buy_orders:
        position_key_orders[(b.ticker, b.strategy_id)].append(b)

    trades: list[TradeReviewItem] = []

    for (ticker, strategy_id), buys in position_key_orders.items():
        total_qty = sum(b.fill_qty for b in buys)
        total_cost = sum(float(b.fill_price) * b.fill_qty for b in buys)
        avg_entry_price = total_cost / total_qty if total_qty > 0 else 0.0
        entry_date = min(b.created_at for b in buys).date()

        # 현재 보유 여부
        held_qty = current_holdings.get(ticker, 0)
        is_open = held_qty > 0

        if is_open:
            # 보유 중: 최신 시세로 미실현 손익 계산
            current_price = _close_on_or_before(db, ticker, today)
            if current_price is None:
                current_price = avg_entry_price
            exit_price = current_price
            exit_proceeds = exit_price * total_qty
            pnl = exit_proceeds - total_cost
            pnl_pct = pnl / total_cost if total_cost > 0 else None
            hold_days = (today - entry_date).days
            status = "open"
            note = "보유 중"
            exit_date_str = None

        else:
            # 청산됨: 매도 주문에서 exit price 탐색
            ticker_sells = sell_by_ticker.get(ticker, [])
            filled_sell = next(
                (s for s in reversed(ticker_sells) if s.fill_price and s.fill_qty > 0), None
            )

            if filled_sell:
                # 체결된 매도 주문 존재
                exit_price = float(filled_sell.fill_price)
                exit_date = filled_sell.created_at.date()
                exit_proceeds = exit_price * total_qty
                pnl = exit_proceeds - total_cost
                pnl_pct = pnl / total_cost if total_cost > 0 else None
                hold_days = (exit_date - entry_date).days
                status = "closed"
                note = None
                exit_date_str = exit_date.isoformat()

            elif ticker_sells:
                # 매도 주문은 있으나 체결가 미기록 (만료) → 매도일 종가 추정
                last_sell = max(ticker_sells, key=lambda s: s.created_at)
                exit_date = last_sell.created_at.date()
                exit_price = _close_on_or_before(db, ticker, exit_date)
                if exit_price:
                    exit_proceeds = exit_price * total_qty
                    pnl = exit_proceeds - total_cost
                    pnl_pct = pnl / total_cost if total_cost > 0 else None
                    status = "estimated_exit"
                    note = "매도 체결가 미기록 → 매도일 종가로 추정"
                else:
                    exit_proceeds = None
                    pnl = None
                    pnl_pct = None
                    status = "estimated_exit"
                    note = "매도 체결가 미기록, 시세 데이터 없음"
                hold_days = (exit_date - entry_date).days
                exit_date_str = exit_date.isoformat()

            else:
                # 매도 기록 없이 포지션 소멸 (비정상)
                exit_price = None
                exit_proceeds = None
                pnl = None
                pnl_pct = None
                hold_days = None
                status = "estimated_exit"
                note = "매도 기록 없음"
                exit_date_str = None

        trades.append(
            TradeReviewItem(
                ticker=ticker,
                name=name_map.get(ticker, ticker),
                strategy_id=strategy_id,
                entry_date=entry_date.isoformat(),
                entry_price=round(avg_entry_price, 2),
                qty=total_qty,
                entry_cost=round(total_cost),
                exit_date=exit_date_str,
                exit_price=round(exit_price) if exit_price else None,
                exit_proceeds=round(exit_proceeds) if exit_proceeds else None,
                pnl=round(pnl) if pnl is not None else None,
                pnl_pct=round(pnl_pct * 100, 2) if pnl_pct is not None else None,
                hold_days=hold_days,
                status=status,
                note=note,
            )
        )

    # 날짜 최신 순 정렬
    trades.sort(key=lambda t: t.entry_date, reverse=True)

    # ── 요약 통계 ─────────────────────────────────────────────────────────
    realized_pnl = sum(t.pnl for t in trades if t.pnl is not None and t.status != "open")
    unrealized_pnl = sum(t.pnl for t in trades if t.pnl is not None and t.status == "open")
    total_pnl = realized_pnl + unrealized_pnl

    closed_trades = [t for t in trades if t.status != "open" and t.pnl is not None]
    open_trades = [t for t in trades if t.status == "open"]
    winning = [t for t in closed_trades if t.pnl and t.pnl > 0]
    losing = [t for t in closed_trades if t.pnl and t.pnl < 0]
    win_rate = len(winning) / len(closed_trades) * 100 if closed_trades else None

    total_return_pct = (current_assets - initial_assets) / initial_assets * 100

    summary = TradeReviewSummary(
        initial_assets=initial_assets,
        current_assets=current_assets,
        total_return_pct=round(total_return_pct, 2),
        total_trades=len(trades),
        open_trades=len(open_trades),
        closed_trades=len(closed_trades),
        winning_trades=len(winning),
        losing_trades=len(losing),
        win_rate=round(win_rate, 1) if win_rate is not None else None,
        realized_pnl=round(realized_pnl),
        unrealized_pnl=round(unrealized_pnl),
        total_pnl=round(total_pnl),
    )

    # ── 전략별 통계 ───────────────────────────────────────────────────────
    strat_groups: dict[str, list[TradeReviewItem]] = defaultdict(list)
    for t in trades:
        strat_groups[t.strategy_id].append(t)

    by_strategy: list[StrategyTradeStats] = []
    for sid, strat_trades in sorted(strat_groups.items()):
        known = [t for t in strat_trades if t.pnl is not None]
        s_wins = [t for t in known if t.pnl and t.pnl > 0]
        s_losses = [t for t in known if t.pnl and t.pnl < 0]
        s_unknown = len(strat_trades) - len(known)
        s_pnl = sum(t.pnl for t in known)
        s_cost = sum(t.entry_cost for t in strat_trades)
        s_win_rate = len(s_wins) / len(known) * 100 if known else None
        s_return_pct = s_pnl / s_cost * 100 if s_cost > 0 and known else None
        by_strategy.append(
            StrategyTradeStats(
                strategy_id=sid,
                total_trades=len(strat_trades),
                wins=len(s_wins),
                losses=len(s_losses),
                unknown=s_unknown,
                win_rate=round(s_win_rate, 1) if s_win_rate is not None else None,
                total_pnl=round(s_pnl),
                total_cost=round(s_cost),
                return_pct=round(s_return_pct, 2) if s_return_pct is not None else None,
            )
        )

    return TradeReviewResponse(
        summary=summary,
        trades=trades,
        by_strategy=by_strategy,
    )
