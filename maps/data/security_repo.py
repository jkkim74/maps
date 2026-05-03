"""Security 도메인 객체 및 security_metadata CRUD.

Security 객체는 as-of-date 메서드를 제공한다.
모든 메서드는 ref_date 이후 정보를 참조하지 않는다.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from maps.common.models import SecurityMetadata


# ---------------------------------------------------------------------------
# 거래정지 · 관리종목 이력 (인메모리, 추후 DB 테이블로 확장)
# ---------------------------------------------------------------------------
@dataclass
class HaltPeriod:
    ticker: str
    start: datetime.date
    end: datetime.date | None  # None = 아직 정지 중


@dataclass
class ManagedPeriod:
    ticker: str
    start: datetime.date
    end: datetime.date | None


# ---------------------------------------------------------------------------
# Security 도메인 객체
# ---------------------------------------------------------------------------
@dataclass
class Security:
    """종목 도메인 객체. as-of-date 조회 메서드를 포함한다."""

    ticker: str
    name: str
    market: str           # KOSPI | KOSDAQ
    security_type: str    # STOCK | ETF | SPAC
    listing_date: datetime.date | None = None
    delisting_date: datetime.date | None = None
    has_adjusted_price: bool = False

    # 이력 데이터 (레포지터리가 주입)
    halt_periods: list[HaltPeriod] = field(default_factory=list)
    managed_periods: list[ManagedPeriod] = field(default_factory=list)
    # {date: avg_20d_turnover_krw}
    turnover_cache: dict[datetime.date, float] = field(default_factory=dict)

    # --- as-of-date 메서드 ---

    def has_adjusted_price_as_of(self, ref_date: datetime.date) -> bool:
        """ref_date 기준으로 수정주가가 있으면 True."""
        return self.has_adjusted_price

    def is_halted_on(self, ref_date: datetime.date) -> bool:
        """ref_date 에 거래정지 중이면 True."""
        for p in self.halt_periods:
            if p.start <= ref_date and (p.end is None or ref_date <= p.end):
                return True
        return False

    def is_managed_on(self, ref_date: datetime.date) -> bool:
        """ref_date 에 관리종목으로 지정되어 있으면 True."""
        for p in self.managed_periods:
            if p.start <= ref_date and (p.end is None or ref_date <= p.end):
                return True
        return False

    def avg_turnover_20d_as_of(self, ref_date: datetime.date) -> float:
        """ref_date 기준 과거 20일 평균 거래대금(원)."""
        return self.turnover_cache.get(ref_date, 0.0)

    def listing_days(self, ref_date: datetime.date) -> int:
        """ref_date 기준 상장 경과일."""
        if self.listing_date is None:
            return 0
        return max(0, (ref_date - self.listing_date).days)


# ---------------------------------------------------------------------------
# 레포지터리
# ---------------------------------------------------------------------------
class SecurityRepository:
    """security_metadata 테이블 CRUD."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def upsert(self, meta: SecurityMetadata) -> None:
        """있으면 갱신, 없으면 삽입."""
        existing = (
            self._db.query(SecurityMetadata)
            .filter(SecurityMetadata.ticker == meta.ticker)
            .first()
        )
        if existing:
            existing.name = meta.name
            existing.market = meta.market
            existing.security_type = meta.security_type
            existing.listing_date = meta.listing_date
            existing.delisting_date = meta.delisting_date
            existing.has_adjusted_price = meta.has_adjusted_price
            existing.updated_at = datetime.datetime.utcnow()
        else:
            self._db.add(meta)
        self._db.commit()

    def get(self, ticker: str) -> SecurityMetadata | None:
        return (
            self._db.query(SecurityMetadata)
            .filter(SecurityMetadata.ticker == ticker)
            .first()
        )

    def list_active(self, as_of: datetime.date) -> list[SecurityMetadata]:
        """as_of 기준으로 상장 중인 종목을 반환한다."""
        q = self._db.query(SecurityMetadata).filter(
            (SecurityMetadata.listing_date == None)  # noqa: E711
            | (SecurityMetadata.listing_date <= as_of)
        )
        # 폐지일이 as_of 이전이면 제외
        results = []
        for row in q.all():
            if row.delisting_date is not None and row.delisting_date < as_of:
                continue
            results.append(row)
        return results

    def to_domain(
        self,
        row: SecurityMetadata,
        halt_periods: list[HaltPeriod] | None = None,
        managed_periods: list[ManagedPeriod] | None = None,
        turnover_cache: dict[datetime.date, float] | None = None,
    ) -> Security:
        """ORM 행을 Security 도메인 객체로 변환한다."""
        return Security(
            ticker=row.ticker,
            name=row.name,
            market=row.market,
            security_type=row.security_type,
            listing_date=row.listing_date,
            delisting_date=row.delisting_date,
            has_adjusted_price=row.has_adjusted_price,
            halt_periods=halt_periods or [],
            managed_periods=managed_periods or [],
            turnover_cache=turnover_cache or {},
        )
