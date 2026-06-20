"""펀더멘털 조회 레포지터리 + 안전마진/가치목표가용 데이터 프로바이더.

`security_fundamental` 테이블(pykrx 적재)을 읽어
- `ValuationMarginScorer` 입력(`ValuationMarginInput`) — 요건 5(안전마진)
- `KostolanyPriceCalculator` 입력(`PriceInput`의 가치 지표) — 요건 7·8(가치 목표가)
을 채운다.

설계 원칙: 데이터가 없으면 None을 채워 스코어러가 중립 처리하도록 하고, 시스템은 중단하지 않는다.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from maps.ai.valuation_margin import ValuationMarginInput
from maps.common.models import SecurityFundamental

logger = logging.getLogger(__name__)

_DEFAULT_LOOKBACK_DAYS = 365


class FundamentalRepository:
    """`security_fundamental` 테이블 as-of-date 조회 전용 레포."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def get_as_of(self, ticker: str, ref_date: datetime.date) -> SecurityFundamental | None:
        """ref_date 이전(포함) 가장 최신 펀더멘털 행을 반환한다."""
        return (
            self._db.query(SecurityFundamental)
            .filter(
                SecurityFundamental.ticker == ticker,
                SecurityFundamental.date <= ref_date,
            )
            .order_by(SecurityFundamental.date.desc())
            .first()
        )

    def _history(
        self, ticker: str, ref_date: datetime.date, lookback_days: int
    ) -> list[SecurityFundamental]:
        start = ref_date - datetime.timedelta(days=lookback_days)
        return (
            self._db.query(SecurityFundamental)
            .filter(
                SecurityFundamental.ticker == ticker,
                SecurityFundamental.date <= ref_date,
                SecurityFundamental.date >= start,
            )
            .all()
        )

    def historical_avg(
        self,
        ticker: str,
        ref_date: datetime.date,
        field: str,
        lookback_days: int = _DEFAULT_LOOKBACK_DAYS,
    ) -> float | None:
        """lookback 기간 내 지정 필드(per/pbr 등)의 평균. 값이 없으면 None."""
        values = [
            getattr(row, field)
            for row in self._history(ticker, ref_date, lookback_days)
            if getattr(row, field) is not None and getattr(row, field) > 0
        ]
        if not values:
            return None
        return round(sum(values) / len(values), 4)

    def historical_band(
        self,
        ticker: str,
        ref_date: datetime.date,
        current_per: float | None,
        lookback_days: int = _DEFAULT_LOOKBACK_DAYS,
    ) -> float | None:
        """현재 PER 의 역사적 밴드 내 위치를 0(바닥)~100(천장)으로 반환한다."""
        if current_per is None or current_per <= 0:
            return None
        values = [
            row.per
            for row in self._history(ticker, ref_date, lookback_days)
            if row.per is not None and row.per > 0
        ]
        if len(values) < 2:
            return None
        lo, hi = min(values), max(values)
        if hi <= lo:
            return None
        pct = (current_per - lo) / (hi - lo) * 100.0
        return round(max(0.0, min(100.0, pct)), 2)


@dataclass
class PriceFundamentals:
    """KostolanyPriceCalculator.PriceInput 에 주입할 가치 지표 묶음."""

    per: float | None = None
    pbr: float | None = None
    eps_forward: float | None = None
    bps: float | None = None
    historical_per_avg: float | None = None
    historical_pbr_avg: float | None = None


class FundamentalValuationProvider:
    """DB(`security_fundamental`) 기반 안전마진/가치목표가 데이터 프로바이더.

    `PlaceholderValuationDataProvider` 와 동일한 `.get(ticker, current_price)` 계약을 유지하므로
    스케줄러에서 드롭인 교체가 가능하다.
    """

    def __init__(self, db: Session, ref_date: datetime.date) -> None:
        self._repo = FundamentalRepository(db)
        self._ref_date = ref_date

    def get(self, ticker: str, current_price: float | None = None) -> ValuationMarginInput:
        """안전마진 스코어 입력을 구성한다. 데이터 없으면 빈 입력(→중립 처리)."""
        row = self._repo.get_as_of(ticker, self._ref_date)
        if row is None:
            return ValuationMarginInput(ticker=ticker, current_price=current_price)

        roe = None
        if row.eps is not None and row.bps and row.bps > 0:
            roe = round(row.eps / row.bps * 100.0, 2)

        band = self._repo.historical_band(ticker, self._ref_date, row.per)

        return ValuationMarginInput(
            ticker=ticker,
            current_price=current_price,
            per=row.per,
            pbr=row.pbr,
            roe=roe,
            historical_valuation_band=band,
        )

    def price_fundamentals(self, ticker: str) -> PriceFundamentals:
        """가치 목표가(value_target) 산출용 펀더멘털을 구성한다."""
        row = self._repo.get_as_of(ticker, self._ref_date)
        if row is None:
            return PriceFundamentals()
        return PriceFundamentals(
            per=row.per,
            pbr=row.pbr,
            eps_forward=row.eps,   # pykrx는 forward EPS 미제공 → 직전 EPS 사용
            bps=row.bps,
            historical_per_avg=self._repo.historical_avg(ticker, self._ref_date, "per"),
            historical_pbr_avg=self._repo.historical_avg(ticker, self._ref_date, "pbr"),
        )
