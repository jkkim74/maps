"""업종 강도 기반 필터 — 최근 N거래일 수익률 상위 업종 선정.

WEAK 시황에서는 방어 업종(유틸리티·헬스케어·필수소비재)을 우선 선택한다.
"""

from __future__ import annotations

import datetime
import logging
from typing import TYPE_CHECKING

from sqlalchemy import func

from maps.common.models import HistoricalOHLCV, SecurityMetadata

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from maps.market.regime import RegimeResult

logger = logging.getLogger(__name__)

_DEFENSIVE_SECTORS: frozenset[str] = frozenset({
    "유틸리티",
    "헬스케어",
    "필수소비재",
    "에너지",
})


class SectorSelector:
    """최근 N거래일 업종 모멘텀 기반 강세 업종을 선정한다."""

    def __init__(self, lookback_days: int = 20, top_n: int = 5) -> None:
        self._lookback_days = lookback_days
        self._top_n = top_n

    def select_strong_sectors(
        self,
        db: "Session",
        ref_date: datetime.date,
        regime: "RegimeResult",
    ) -> list[str]:
        """강세 업종 목록을 반환한다.

        Returns:
            업종명 리스트. 빈 리스트이면 필터 없이 전체 유니버스 사용.
        """
        cutoff = ref_date - datetime.timedelta(days=self._lookback_days * 2)

        # 최근 N거래일 시작 가격 + 현재 가격을 업종별로 집계
        # 서브쿼리: 종목별 (start_close, end_close)
        rows = (
            db.query(
                SecurityMetadata.sector,
                func.avg(HistoricalOHLCV.close).label("avg_close"),
                func.count(HistoricalOHLCV.ticker.distinct()).label("ticker_count"),
            )
            .join(
                SecurityMetadata,
                HistoricalOHLCV.ticker == SecurityMetadata.ticker,
            )
            .filter(
                SecurityMetadata.sector.isnot(None),
                HistoricalOHLCV.date >= cutoff,
                HistoricalOHLCV.date <= ref_date,
            )
            .group_by(SecurityMetadata.sector)
            .having(func.count(HistoricalOHLCV.ticker.distinct()) >= 3)
            .all()
        )

        if not rows:
            logger.info("업종 데이터 없음 — 섹터 필터 미적용")
            return []

        # 종가 기반 평균 모멘텀 계산이 단순 avg_close로는 의미가 없으므로
        # 각 업종 내 종목들의 기간 수익률 평균을 계산
        sector_returns = self._calc_sector_returns(db, ref_date, cutoff)

        if not sector_returns:
            logger.info("업종 수익률 계산 실패 — 섹터 필터 미적용")
            return []

        from maps.market.regime import RegimeLabel  # 지연 임포트(순환 방지)

        is_weak = regime.regime == RegimeLabel.WEAK
        sorted_sectors = sorted(sector_returns.items(), key=lambda x: x[1], reverse=True)

        if is_weak:
            # WEAK 시황: 방어 업종을 우선 포함
            defensive = [s for s, _ in sorted_sectors if s in _DEFENSIVE_SECTORS]
            others = [s for s, _ in sorted_sectors if s not in _DEFENSIVE_SECTORS]
            candidates = defensive + others
        else:
            candidates = [s for s, _ in sorted_sectors]

        selected = candidates[: self._top_n]
        logger.info(
            "강세 업종 선정 [%s, regime=%s]: %s",
            ref_date,
            regime.regime.value,
            selected,
        )
        return selected

    def _calc_sector_returns(
        self,
        db: "Session",
        ref_date: datetime.date,
        cutoff: datetime.date,
    ) -> dict[str, float]:
        """업종별 기간 평균 수익률을 계산한다."""
        # ticker별 (start_close, end_close) 조회
        start_closes = (
            db.query(
                HistoricalOHLCV.ticker,
                func.min(HistoricalOHLCV.date).label("min_date"),
            )
            .filter(
                HistoricalOHLCV.date >= cutoff,
                HistoricalOHLCV.date <= ref_date,
            )
            .group_by(HistoricalOHLCV.ticker)
            .subquery()
        )

        # 시작일 종가
        start_q = (
            db.query(
                HistoricalOHLCV.ticker,
                HistoricalOHLCV.close.label("start_close"),
            )
            .join(
                start_closes,
                (HistoricalOHLCV.ticker == start_closes.c.ticker)
                & (HistoricalOHLCV.date == start_closes.c.min_date),
            )
            .subquery()
        )

        # 종료일(ref_date) 종가
        end_q = (
            db.query(
                HistoricalOHLCV.ticker,
                HistoricalOHLCV.close.label("end_close"),
            )
            .filter(HistoricalOHLCV.date == ref_date)
            .subquery()
        )

        # 업종별 평균 수익률
        result = (
            db.query(
                SecurityMetadata.sector,
                func.avg(
                    (end_q.c.end_close - start_q.c.start_close) / start_q.c.start_close
                ).label("avg_return"),
            )
            .join(start_q, SecurityMetadata.ticker == start_q.c.ticker)
            .join(end_q, SecurityMetadata.ticker == end_q.c.ticker)
            .filter(
                SecurityMetadata.sector.isnot(None),
                start_q.c.start_close > 0,
            )
            .group_by(SecurityMetadata.sector)
            .having(func.count(SecurityMetadata.ticker) >= 3)
            .all()
        )

        return {row.sector: float(row.avg_return) for row in result if row.avg_return is not None}
