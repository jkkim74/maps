"""데이터 수집 오케스트레이터."""

from __future__ import annotations

import datetime
import logging

import pandas as pd
from sqlalchemy.orm import Session

from maps.common.exceptions import DataCollectionError
from maps.common.models import CollectionLog, SecurityMetadata
from maps.data.krx_adapter import CollectionResult, KRXAdapterBase, MockKRXAdapter

logger = logging.getLogger(__name__)


def _business_days(
    start: datetime.date, end: datetime.date
) -> list[datetime.date]:
    return [
        d.date()
        for d in pd.bdate_range(start=start, end=end)
    ]


class DataCollector:
    """KRX API + 증권사 API 일별 수집 오케스트레이터.

    broker 가 None이면 수정주가 폴백 없이 KRX 단독 수집한다.
    """

    def __init__(
        self,
        krx: KRXAdapterBase,
        db: Session,
        broker=None,  # BrokerAdapter | None — Phase 4 이후 사용
    ) -> None:
        self._krx = krx
        self._db = db
        self._broker = broker

    def collect_daily(self, ref_date: datetime.date) -> CollectionResult:
        """ref_date 하루치 OHLCV + 메타 수집 후 DB에 적재한다.

        수정주가가 없고 broker가 있으면 broker로 폴백한다.
        """
        logger.info("수집 시작: %s", ref_date)
        try:
            ohlcv = self._krx.get_ohlcv(ref_date)
            meta = self._krx.get_security_meta(ref_date)
            halts = self._krx.get_halt_list(ref_date)
            managed = self._krx.get_managed_list(ref_date)

            # 수정주가 폴백
            missing_adj = [o for o in ohlcv if not o.has_adjusted]
            if missing_adj and self._broker is not None:
                logger.warning("수정주가 누락 %d건 — broker 폴백 (Phase 4 구현 예정)", len(missing_adj))
                # Phase 4: broker.get_adjusted_ohlcv(ref_date) 연동 후 구현

            result = CollectionResult(
                ref_date=ref_date,
                ohlcv=ohlcv,
                meta=meta,
                halts=halts,
                managed=managed,
            )
            self._upsert_meta(meta)
            self._write_log(ref_date, "success", len(ohlcv))
            return result

        except Exception as exc:
            self._write_log(ref_date, "failed", 0, str(exc))
            raise DataCollectionError(f"수집 실패 [{ref_date}]: {exc}") from exc

    def collect_range(
        self, start: datetime.date, end: datetime.date
    ) -> list[CollectionResult]:
        """기간 배치 수집. 단일 날짜 실패는 경고만 하고 계속 진행한다."""
        results: list[CollectionResult] = []
        for day in _business_days(start, end):
            try:
                results.append(self.collect_daily(day))
            except DataCollectionError as exc:
                logger.warning("수집 실패 (스킵): %s — %s", day, exc)
        return results

    def _upsert_meta(self, meta_list) -> None:
        """종목 메타를 security_metadata 테이블에 upsert한다."""
        for m in meta_list:
            existing = (
                self._db.query(SecurityMetadata)
                .filter(SecurityMetadata.ticker == m.ticker)
                .first()
            )
            if existing:
                existing.name = m.name
                existing.market = m.market
                existing.security_type = m.security_type
                existing.listing_date = m.listing_date
                existing.delisting_date = m.delisting_date
                existing.updated_at = datetime.datetime.utcnow()
            else:
                self._db.add(
                    SecurityMetadata(
                        ticker=m.ticker,
                        name=m.name,
                        market=m.market,
                        security_type=m.security_type,
                        listing_date=m.listing_date,
                        delisting_date=m.delisting_date,
                        has_adjusted_price=True,
                    )
                )
        self._db.commit()

    def _write_log(
        self,
        ref_date: datetime.date,
        status: str,
        items: int,
        note: str | None = None,
    ) -> None:
        """collection_log 테이블에 감사 로그를 기록한다."""
        self._db.add(
            CollectionLog(
                ref_date=ref_date,
                source="krx",
                status=status,
                items=items,
                note=note,
            )
        )
        self._db.commit()
