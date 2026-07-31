"""보유 중인 종목의 매수 주문에 진입 시점 ATR(14)을 채우는 일회성 스크립트.

`order_log.atr14` 도입(2026-07-31) 이전에 체결된 매수 주문에는 이 값이 없다.
값이 없으면 청산·화면이 **그날의 ATR** 로 손절가를 다시 계산하는데, 사이징은
진입 시점 한 번뿐이라 ATR 이 커지면 손절폭만 넓어져 계좌 위험이 예산을 넘어간다
(089860: 의도 0.50% → 실제 0.55%).

ATR 은 `OperationalPipeline._latest_strategy_signal(ref_date=진입일)` 로 구한다.
청산 경로와 **같은 400봉 lookback** 을 쓰는 유일한 방법이라 별도 구현을 만들지 않는다.
Wilder 평활은 워밍업 길이에 따라 값이 달라져서, 여기서 20봉으로 재면 백필값이
실제 청산 기준과 어긋난다.

안전장치:
- 기본은 dry-run. 실제 반영은 ``--apply`` 필요.
- **현재 보유 중인 종목의 최신 체결 매수 행만** 대상으로 한다. 이미 청산된 과거
  주문은 소급하지 않는다 (감사 로그를 소급 수정하지 않는다는 원칙).
- 이미 ``atr14`` 가 있는 행은 건너뛴다 — 재실행해도 값이 바뀌지 않는다.

사용법 (프로젝트 루트 / 서버 venv 에서):
    python scripts/backfill_entry_atr.py            # dry-run: 대상과 계산값만 출력
    python scripts/backfill_entry_atr.py --apply    # 실제 DB 반영
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from maps.common.db import SessionLocal
from maps.common.models import OrderLog, PortfolioSnapshot
from maps.execution.broker_adapter import OrderSide
from maps.ops.scheduler import OperationalPipeline
from maps.strategy.live_rules import effective_stop_price


def _held_tickers(db) -> set[str]:
    """최신 포트폴리오 스냅샷의 보유 종목. 스냅샷이 없으면 빈 집합."""
    snap = (
        db.query(PortfolioSnapshot)
        .filter(PortfolioSnapshot.holdings.isnot(None))
        .order_by(PortfolioSnapshot.ref_date.desc())
        .first()
    )
    holdings = (snap.holdings or {}) if snap else {}
    if not isinstance(holdings, dict):
        return set()
    return {ticker for ticker, qty in holdings.items() if qty and qty > 0}


def _latest_buy_rows(db, tickers: set[str]) -> list[OrderLog]:
    """종목별 최신 체결 매수 행 (청산 경로가 진입가로 고르는 것과 같은 규칙)."""
    if not tickers:
        return []
    rows = (
        db.query(OrderLog)
        .filter(OrderLog.ticker.in_(tickers))
        .filter(OrderLog.side == OrderSide.BUY.value)
        .filter(OrderLog.status.in_(["filled", "partially_filled"]))
        .order_by(OrderLog.created_at.desc(), OrderLog.id.desc())
        .all()
    )
    latest: dict[str, OrderLog] = {}
    for row in rows:
        if row.ticker not in latest:
            latest[row.ticker] = row
    return list(latest.values())


def backfill(*, apply: bool) -> int:
    """보유 종목의 매수 행에 진입일 ATR 을 채운다. 변경(예정) 건수를 반환한다."""
    db = SessionLocal()
    changed = 0
    try:
        pipeline = OperationalPipeline()
        rows = _latest_buy_rows(db, _held_tickers(db))
        if not rows:
            print("대상 없음 — 보유 종목이 없거나 체결된 매수 기록이 없습니다.")
            return 0

        today = dt.date.today()
        print(f"{'티커':<8} {'전략':<22} {'진입일':<12} {'진입가':>10} "
              f"{'진입ATR':>9} {'오늘ATR':>9} {'손절(현재)':>11} {'손절(고정후)':>12}")
        print("-" * 100)
        for row in sorted(rows, key=lambda r: r.ticker):
            entry_price = row.fill_price or row.order_price
            entry_date = row.created_at.date() if row.created_at else None
            if row.atr14:
                print(f"{row.ticker:<8} {row.strategy_id or '':<22} {'(이미 있음)':<12} "
                      f"{entry_price or 0:>10,.0f} {row.atr14:>9,.1f}")
                continue
            if not row.strategy_id or entry_date is None or not entry_price:
                print(f"{row.ticker:<8} {'(전략·진입가·진입일 누락 — 건너뜀)'}")
                continue

            signal = pipeline._latest_strategy_signal(
                db, ticker=row.ticker, strategy_id=row.strategy_id, ref_date=entry_date
            )
            atr14 = signal.atr14 if signal is not None else None
            if not atr14:
                print(f"{row.ticker:<8} {row.strategy_id or '':<22} {entry_date!s:<12} "
                      f"{entry_price:>10,.0f} {'ATR 산출 불가 — 건너뜀':>9}")
                continue

            # 현재 손절가는 '오늘의 ATR' 로 재계산된 값이다 — 그게 이번에 고치는 대상이다.
            today_signal = pipeline._latest_strategy_signal(
                db, ticker=row.ticker, strategy_id=row.strategy_id, ref_date=today
            )
            today_atr = today_signal.atr14 if today_signal is not None else None
            before = effective_stop_price(row.strategy_id, entry_price, today_atr)
            after = effective_stop_price(row.strategy_id, entry_price, atr14)
            print(f"{row.ticker:<8} {row.strategy_id:<22} {entry_date!s:<12} "
                  f"{entry_price:>10,.0f} {atr14:>9,.1f} {today_atr or 0:>9,.1f} "
                  f"{before or 0:>11,.0f} {after or 0:>12,.0f}")
            row.atr14 = float(atr14)
            changed += 1

        if apply and changed:
            db.commit()
            print(f"\n반영 완료: {changed}건")
        elif changed:
            db.rollback()
            print(f"\ndry-run: {changed}건이 변경 대상입니다. 반영하려면 --apply 를 붙이세요.")
        else:
            print("\n변경 대상 없음.")
        return changed
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="보유 종목 매수 주문에 진입 시점 ATR 백필")
    parser.add_argument("--apply", action="store_true", help="실제 DB 에 반영한다 (기본은 dry-run)")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    backfill(apply=args.apply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
