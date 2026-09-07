#!/usr/bin/env python3
"""`security_metadata.listing_date` 를 KRX 전종목 기본정보로 채운다.

2026-09-07 까지 운영 `security_metadata` 2,790행의 상장일이 **전부 NULL** 이었다. pykrx
ticker-list 는 상장일을 주지 않고, 수집기는 매일 None 으로 덮어썼다. 상한가 V1 자격
판정(`limit_up/runtime.py`)은 상장일 NULL 을 fail-closed 로 막으므로 8/29 가동 이후
후보가 한 건도 수락되지 않았다.

수집기(`collect_daily`)는 이제 상장일을 채우지만 16:40 에 돈다. 이 스크립트는 그때까지
기다리지 않고 **상장일 컬럼 하나만** 즉시 갱신한다 — `collect_daily` 를 장중에 돌리면
미완성 봉이 OHLCV 에 들어가고 `_upsert_meta` 가 `has_adjusted_price` 까지 덮어쓴다.

- 값이 이미 같은 행은 건드리지 않는다(멱등).
- KRX 프레임에 없는 종목(상폐 추정)은 NULL 로 남기고 건수·샘플을 보고한다.
- 기본은 dry-run. 대상 건수를 확인한 뒤 `--apply` 로만 쓴다.

사용법:

    python scripts/backfill_listing_dates.py
    python scripts/backfill_listing_dates.py --apply
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.orm import Session

from maps.common.db import SessionLocal
from maps.common.models import SecurityMetadata
from maps.data.krx_adapter import fetch_listing_dates


@dataclass
class BackfillReport:
    """한 번의 실행 결과."""

    total: int = 0
    updated: int = 0
    unchanged: int = 0
    still_missing: list[str] = field(default_factory=list)


def apply_listing_dates(
    db: Session, listing: dict[str, dt.date], *, apply: bool
) -> BackfillReport:
    """상장일이 비었거나 다른 행만 갱신한다. ``apply=False`` 면 세지만 쓰지 않는다.

    :param db: SQLAlchemy 세션.
    :param listing: 단축코드 → 상장일 (KRX 전종목 기본정보).
    :param apply: True 일 때만 commit 한다.
    :return: 갱신·유지·결측 집계.
    """
    report = BackfillReport()
    for row in db.query(SecurityMetadata).order_by(SecurityMetadata.ticker).all():
        report.total += 1
        known = listing.get(row.ticker)
        if known is None:
            report.still_missing.append(row.ticker)
            continue
        if row.listing_date == known:
            report.unchanged += 1
            continue
        report.updated += 1
        if apply:
            row.listing_date = known
    if apply:
        db.commit()
    else:
        db.rollback()
    return report


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--apply", action="store_true", help="실제로 DB 에 쓴다 (기본 dry-run)")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    """스크립트 진입점."""
    args = _parse_args(argv)
    listing = fetch_listing_dates()
    print(f"KRX 전종목 기본정보: {len(listing)}종목의 상장일 수신")
    if not listing:
        print("상장일을 받지 못했다 — KRX 로그인·회로차단기 상태를 확인할 것. 아무것도 쓰지 않는다.")
        return 1

    db = SessionLocal()
    try:
        report = apply_listing_dates(db, listing, apply=args.apply)
    finally:
        db.close()

    mode = "적용" if args.apply else "dry-run"
    print(f"[{mode}] 메타 {report.total}행 — 갱신 {report.updated}, 유지 {report.unchanged}, "
          f"KRX 에 없음 {len(report.still_missing)}")
    if report.still_missing:
        print(f"  KRX 에 없는 종목(상폐 추정, NULL 유지) 샘플: {report.still_missing[:10]}")
    if not args.apply and report.updated:
        print("쓰려면 --apply 를 붙여 다시 실행한다.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
