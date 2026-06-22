"""주문 정산 리포트 — order_log 실적을 KST 일자 기준으로 집계하고 미체결을 진단한다.

매수/매도 주문의 제출·체결·만료 현황과 체결률을 집계하고, 미체결(expired/pending)
주문에 대해 지정가가 당일 고저가 범위로 도달 가능했는지(reachable)를 판정한다.
브로커 호출 없이 DB(order_log + historical_ohlcv)만으로 동작한다.
"""

from __future__ import annotations

import datetime as dt
import zoneinfo
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from maps.common.models import HistoricalOHLCV, OrderLog

_KST = zoneinfo.ZoneInfo("Asia/Seoul")
_UNFILLED_STATUSES = ("expired", "pending", "partially_filled")


def _kst_date(created_at: dt.datetime) -> dt.date:
    """naive UTC created_at을 KST 거래일로 변환한다 (08:55 KST 주문 = 전날 23:55 UTC)."""
    return (created_at + dt.timedelta(hours=9)).date()


@dataclass
class SideStat:
    """매수 또는 매도 한 변의 상태별 집계."""

    side: str
    submitted: int = 0
    filled: int = 0
    partially_filled: int = 0
    expired: int = 0
    rejected: int = 0
    other: int = 0

    @property
    def fill_rate(self) -> float:
        """완전체결 비율 (filled / submitted)."""
        return round(self.filled / self.submitted, 4) if self.submitted else 0.0


@dataclass
class UnfilledOrder:
    """미체결 주문 1건 + 당일 시세 도달 가능성 진단."""

    kst_date: str
    strategy_id: str
    ticker: str
    side: str
    status: str
    qty: int
    order_price: float | None
    day_high: float | None
    day_low: float | None
    day_close: float | None
    reachable: bool | None  # 매수: low<=limit, 매도: high>=limit. 데이터/지정가 없으면 None


@dataclass
class ReconciliationSummary:
    """정산 리포트 결과."""

    start_kst: str
    end_kst: str
    total_orders: int
    by_side: list[SideStat] = field(default_factory=list)
    unfilled: list[UnfilledOrder] = field(default_factory=list)


def _classify(stat: SideStat, status: str) -> None:
    stat.submitted += 1
    if status == "filled":
        stat.filled += 1
    elif status == "partially_filled":
        stat.partially_filled += 1
    elif status == "expired":
        stat.expired += 1
    elif status == "rejected":
        stat.rejected += 1
    else:
        stat.other += 1


def _reachable(side: str, order_price: float | None, high: float | None, low: float | None) -> bool | None:
    """지정가가 당일 고저가 범위로 도달 가능했는지 판정한다."""
    if order_price is None or order_price <= 0 or high is None or low is None:
        return None
    if side == "buy":
        return low <= order_price          # 매수 지정가는 저가가 닿으면 체결
    return high >= order_price             # 매도 지정가는 고가가 닿으면 체결


def build_reconciliation(
    db: Session,
    *,
    days: int = 30,
    end: dt.date | None = None,
) -> ReconciliationSummary:
    """최근 `days`일(KST) 주문 정산 요약을 생성한다."""
    end = end or dt.datetime.now(_KST).date()
    start = end - dt.timedelta(days=days)
    # KST 일자 경계를 UTC로 변환해 조회 (created_at은 naive UTC)
    start_utc = dt.datetime.combine(start, dt.time.min) - dt.timedelta(hours=9)
    end_utc = dt.datetime.combine(end + dt.timedelta(days=1), dt.time.min) - dt.timedelta(hours=9)

    rows = (
        db.query(OrderLog)
        .filter(OrderLog.created_at >= start_utc, OrderLog.created_at < end_utc)
        .order_by(OrderLog.created_at.desc())
        .all()
    )

    stats: dict[str, SideStat] = {"buy": SideStat(side="buy"), "sell": SideStat(side="sell")}
    unfilled: list[UnfilledOrder] = []

    # 미체결 진단용 당일 OHLCV 캐시: (ticker, kst_date) → row
    ohlcv_cache: dict[tuple[str, dt.date], HistoricalOHLCV | None] = {}

    def _day_ohlcv(ticker: str, day: dt.date) -> HistoricalOHLCV | None:
        key = (ticker, day)
        if key not in ohlcv_cache:
            ohlcv_cache[key] = (
                db.query(HistoricalOHLCV)
                .filter(HistoricalOHLCV.ticker == ticker, HistoricalOHLCV.date == day)
                .first()
            )
        return ohlcv_cache[key]

    for r in rows:
        side = (r.side or "").lower()
        stat = stats.get(side)
        if stat is not None:
            _classify(stat, r.status or "")

        if (r.status or "") in _UNFILLED_STATUSES:
            day = _kst_date(r.created_at)
            ohlcv = _day_ohlcv(r.ticker, day)
            high = float(ohlcv.high) if ohlcv and ohlcv.high else None
            low = float(ohlcv.low) if ohlcv and ohlcv.low else None
            close = float(ohlcv.close) if ohlcv and ohlcv.close else None
            unfilled.append(
                UnfilledOrder(
                    kst_date=day.isoformat(),
                    strategy_id=r.strategy_id or "",
                    ticker=r.ticker,
                    side=side,
                    status=r.status or "",
                    qty=r.qty or 0,
                    order_price=r.order_price,
                    day_high=high,
                    day_low=low,
                    day_close=close,
                    reachable=_reachable(side, r.order_price, high, low),
                )
            )

    return ReconciliationSummary(
        start_kst=start.isoformat(),
        end_kst=end.isoformat(),
        total_orders=len(rows),
        by_side=[stats["buy"], stats["sell"]],
        unfilled=unfilled,
    )


def format_reconciliation_text(summary: ReconciliationSummary) -> str:
    """정산 요약을 사람이 읽기 쉬운 텍스트로 변환한다."""
    lines = [
        f"[주문 정산 리포트 {summary.start_kst} ~ {summary.end_kst} (KST)]",
        f"  총 주문 {summary.total_orders}건",
        f"  {'side':<6}{'제출':>5}{'체결':>5}{'부분':>5}{'만료':>5}{'거부':>5}{'체결률':>8}",
    ]
    for s in summary.by_side:
        lines.append(
            f"  {s.side:<6}{s.submitted:>5}{s.filled:>5}{s.partially_filled:>5}"
            f"{s.expired:>5}{s.rejected:>5}{s.fill_rate:>8.0%}"
        )

    if summary.unfilled:
        reachable_n = sum(1 for u in summary.unfilled if u.reachable is True)
        unreachable_n = sum(1 for u in summary.unfilled if u.reachable is False)
        unknown_n = sum(1 for u in summary.unfilled if u.reachable is None)
        lines.append(
            f"\n  미체결 {len(summary.unfilled)}건 — "
            f"도달가능 {reachable_n} / 도달불가 {unreachable_n} / 판정불가 {unknown_n}"
        )
        lines.append("  (도달가능=지정가가 당일 고저가에 닿았는데 미체결 → 별도 원인 의심)")
        for u in summary.unfilled[:20]:
            price = f"{u.order_price:,.0f}" if u.order_price else "None"
            rng = (
                f"L{u.day_low:,.0f}~H{u.day_high:,.0f}"
                if u.day_low and u.day_high else "n/a"
            )
            reach = {True: "도달가능", False: "도달불가", None: "판정불가"}[u.reachable]
            lines.append(
                f"    {u.kst_date} {u.side:<4} {u.ticker} {u.strategy_id} "
                f"지정가={price} 당일={rng} → {reach} ({u.status})"
            )
    return "\n".join(lines)
