"""``scripts/backfill_listing_dates.py`` — 상장일만 갱신하고 나머지 컬럼은 건드리지 않는다."""

from __future__ import annotations

import datetime
import importlib.util
import sys
from pathlib import Path

from maps.common.models import SecurityMetadata


def _load_script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "backfill_listing_dates.py"
    spec = importlib.util.spec_from_file_location("backfill_listing_dates", path)
    module = importlib.util.module_from_spec(spec)
    # dataclass 는 from __future__ annotations 해석에 sys.modules 의 모듈 dict 를 쓴다.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _row(ticker: str, listing_date: datetime.date | None, *, has_adjusted_price: bool = True) -> SecurityMetadata:
    return SecurityMetadata(
        ticker=ticker,
        name="종목",
        market="KOSDAQ",
        security_type="STOCK",
        listing_date=listing_date,
        has_adjusted_price=has_adjusted_price,
    )


def test_apply_listing_dates_fills_only_missing_or_changed_rows(db) -> None:
    script = _load_script()
    db.add_all([_row("005930", None), _row("014950", datetime.date(2025, 10, 27)), _row("999999", None)])
    db.commit()
    listing = {"005930": datetime.date(1975, 6, 11), "014950": datetime.date(2025, 10, 27), "000001": datetime.date(2020, 1, 1)}

    report = script.apply_listing_dates(db, listing, apply=True)

    assert report.updated == 1
    assert report.still_missing == ["999999"]
    assert db.query(SecurityMetadata).filter_by(ticker="005930").one().listing_date == datetime.date(1975, 6, 11)
    # 다른 컬럼은 그대로 — collect_daily 의 _upsert_meta 가 has_adjusted_price 를 덮는 것과 다르다.
    assert db.query(SecurityMetadata).filter_by(ticker="005930").one().has_adjusted_price is True


def test_apply_listing_dates_dry_run_writes_nothing(db) -> None:
    script = _load_script()
    db.add(_row("005930", None))
    db.commit()

    report = script.apply_listing_dates(db, {"005930": datetime.date(1975, 6, 11)}, apply=False)

    assert report.updated == 1  # 대상 건수는 보고한다
    db.expire_all()
    assert db.query(SecurityMetadata).filter_by(ticker="005930").one().listing_date is None
