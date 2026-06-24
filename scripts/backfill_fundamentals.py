"""security_fundamental 백필 스크립트 (Naver 대체 소스).

KRX MDC 펀더멘털 엔드포인트가 ``400 LOGOUT`` 으로 차단된 환경에서, Naver 모바일
통합 API로 PER/PBR/EPS/BPS/DIV/DPS 스냅샷을 가져와 `security_fundamental` 에 적재한다.

사용법 (프로젝트 루트에서):
    python scripts/backfill_fundamentals.py                 # 전체 STOCK 유니버스
    python scripts/backfill_fundamentals.py 005930 000660   # 특정 종목만
    python scripts/backfill_fundamentals.py --date 2026-06-23 005930

Naver는 현재 스냅샷만 제공하므로 기본 적재 일자는 오늘(KST)이다. ``--date`` 로
명시할 수 있으나, 과거 일자에 대해서도 *현재* 지표가 적재됨에 유의한다(시계열 백필 불가).
"""

from __future__ import annotations

import argparse
import datetime
import sys

from maps.common.db import SessionLocal
from maps.common.models import SecurityMetadata
from maps.data.collector import DataCollector
from maps.data.krx_adapter import MockKRXAdapter
from maps.data.naver_fundamental import NaverFundamentalAdapter


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Naver 기반 펀더멘털 백필")
    parser.add_argument(
        "tickers",
        nargs="*",
        help="대상 종목코드. 미지정 시 security_metadata 의 STOCK 전체.",
    )
    parser.add_argument(
        "--date",
        default=None,
        help="적재 일자(YYYY-MM-DD). 기본=오늘.",
    )
    parser.add_argument(
        "--workers", type=int, default=8, help="병렬 요청 수 (기본 8)."
    )
    return parser.parse_args(argv)


def _resolve_tickers(db, explicit: list[str]) -> list[str]:
    """명시 종목이 없으면 STOCK 유니버스(ETF/SPAC 제외)를 반환한다."""
    if explicit:
        return explicit
    rows = (
        db.query(SecurityMetadata.ticker)
        .filter(SecurityMetadata.security_type == "STOCK")
        .all()
    )
    return [r[0] for r in rows]


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    ref_date = (
        datetime.date.fromisoformat(args.date)
        if args.date
        else datetime.date.today()
    )

    db = SessionLocal()
    try:
        tickers = _resolve_tickers(db, args.tickers)
        if not tickers:
            print("대상 종목이 없습니다. (security_metadata 비어있음?)")
            return 1
        print(f"[backfill] {ref_date} 펀더멘털 백필 시작 — {len(tickers)}종목")

        # DataCollector는 KRX 어댑터를 요구하지만 스냅샷 경로는 Naver만 사용한다.
        collector = DataCollector(krx=MockKRXAdapter(seed_tickers=tickers), db=db)
        adapter = NaverFundamentalAdapter(max_workers=args.workers)
        result = collector.collect_fundamental_snapshot(adapter, ref_date, tickers)

        print(
            f"[backfill] 완료 — 요청 {result['requested']}, "
            f"수집 {result['fetched']}, 적재 {result['rows']}"
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
