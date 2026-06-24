"""데이터 수집 오케스트레이터."""

from __future__ import annotations

import datetime
import logging
from typing import TYPE_CHECKING

import pandas as pd
from sqlalchemy.orm import Session

from maps.common.exceptions import DataCollectionError
from maps.common.models import (
    CollectionLog,
    HistoricalOHLCV,
    SecurityFundamental,
    SecurityMetadata,
)
from maps.data.krx_adapter import CollectionResult, KRXAdapterBase, MockKRXAdapter

if TYPE_CHECKING:
    from maps.data.naver_fundamental import NaverFundamentalAdapter

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

            try:
                sectors = self._krx.get_sector_classifications(ref_date)
            except Exception as exc:
                logger.warning("업종 분류 수집 실패 (스킵): %s", exc)
                sectors = {}

            result = CollectionResult(
                ref_date=ref_date,
                ohlcv=ohlcv,
                meta=meta,
                halts=halts,
                managed=managed,
            )
            try:
                fundamentals = self._krx.get_fundamental(ref_date)
            except Exception as exc:
                logger.warning("펀더멘털 수집 실패 (스킵): %s", exc)
                fundamentals = []

            self._upsert_meta(
                meta,
                {row.ticker: row.has_adjusted for row in ohlcv},
                sectors,
            )
            saved_rows = self._upsert_ohlcv(ohlcv)
            self._upsert_fundamentals(fundamentals)
            self._write_log(ref_date, "success", saved_rows)
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

    def collect_ohlcv_history(self, start: datetime.date, end: datetime.date) -> dict:
        """기간 OHLCV만 백필한다.

        검증/WFA/MC용 히스토리 적재 경로다. 매일 종목 메타를 다시
        수집하지 않고 가격 데이터만 upsert한다.
        """
        if start > end:
            raise DataCollectionError(f"백필 시작일이 종료일보다 늦습니다: {start} > {end}")

        requested_days = _business_days(start, end)
        success_days = 0
        failed_days = 0
        total_rows = 0
        failures: list[dict[str, str]] = []

        for day in requested_days:
            try:
                rows = self._krx.get_ohlcv(day)
                saved_rows = self._upsert_ohlcv(rows)
                self._write_log(day, "success", saved_rows, source="krx.history")
                success_days += 1
                total_rows += saved_rows
            except Exception as exc:
                failed_days += 1
                failures.append({"date": day.isoformat(), "error": str(exc)})
                self._write_log(day, "failed", 0, str(exc), source="krx.history")
                logger.warning("OHLCV 백필 실패: %s — %s", day, exc)

        return {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "business_days": len(requested_days),
            "success_days": success_days,
            "failed_days": failed_days,
            "rows": total_rows,
            "failures": failures[:10],
        }

    def collect_fundamental_history(self, start: datetime.date, end: datetime.date) -> dict:
        """기간 펀더멘털만 백필한다.

        value_target/역사적 밸류 밴드(요건 7·8)는 과거 PER/PBR 히스토리가 있어야 의미가 있다.
        가격 백필(`collect_ohlcv_history`)과 분리된 일자별 펀더멘털 적재 경로다.
        """
        if start > end:
            raise DataCollectionError(f"백필 시작일이 종료일보다 늦습니다: {start} > {end}")

        requested_days = _business_days(start, end)
        success_days = 0
        failed_days = 0
        total_rows = 0
        for day in requested_days:
            try:
                rows = self._krx.get_fundamental(day)
                saved = self._upsert_fundamentals(rows)
                self._write_log(day, "success", saved, source="krx.fundamental")
                success_days += 1
                total_rows += saved
            except Exception as exc:
                failed_days += 1
                self._write_log(day, "failed", 0, str(exc), source="krx.fundamental")
                logger.warning("펀더멘털 백필 실패: %s — %s", day, exc)

        return {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "business_days": len(requested_days),
            "success_days": success_days,
            "failed_days": failed_days,
            "rows": total_rows,
        }

    def _upsert_meta(
        self,
        meta_list,
        adjusted_by_ticker: dict[str, bool] | None = None,
        sector_by_ticker: dict[str, str] | None = None,
    ) -> None:
        """종목 메타를 security_metadata 테이블에 upsert한다."""
        adjusted_by_ticker = adjusted_by_ticker or {}
        sector_by_ticker = sector_by_ticker or {}
        for m in meta_list:
            has_adjusted_price = adjusted_by_ticker.get(m.ticker, False)
            sector = sector_by_ticker.get(m.ticker)
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
                existing.has_adjusted_price = has_adjusted_price
                if sector is not None:
                    existing.sector = sector
                existing.updated_at = datetime.datetime.now(datetime.timezone.utc)
            else:
                self._db.add(
                    SecurityMetadata(
                        ticker=m.ticker,
                        name=m.name,
                        market=m.market,
                        security_type=m.security_type,
                        listing_date=m.listing_date,
                        delisting_date=m.delisting_date,
                        has_adjusted_price=has_adjusted_price,
                        sector=sector,
                    )
                )
        self._db.commit()

    def _upsert_ohlcv(self, rows) -> int:
        """일봉 OHLCV를 historical_ohlcv 테이블에 upsert한다."""
        saved_rows = 0
        for row in rows:
            if not self._is_valid_ohlcv(row):
                continue
            saved_rows += 1
            existing = (
                self._db.query(HistoricalOHLCV)
                .filter(
                    HistoricalOHLCV.ticker == row.ticker,
                    HistoricalOHLCV.date == row.date,
                )
                .first()
            )
            if existing:
                existing.open = row.open
                existing.high = row.high
                existing.low = row.low
                existing.close = row.close
                existing.volume = row.volume
                existing.adj_close = row.adj_close
                existing.source = "krx"
                existing.updated_at = datetime.datetime.now(datetime.timezone.utc)
            else:
                self._db.add(
                    HistoricalOHLCV(
                        ticker=row.ticker,
                        date=row.date,
                        open=row.open,
                        high=row.high,
                        low=row.low,
                        close=row.close,
                        volume=row.volume,
                        adj_close=row.adj_close,
                        source="krx",
                    )
                )
        self._db.commit()
        return saved_rows

    def _upsert_fundamentals(self, rows, source: str = "pykrx") -> int:
        """일별 펀더멘털을 security_fundamental 테이블에 upsert한다.

        :param source: 데이터 출처 태그 (``pykrx`` 기본, Naver 대체 시 ``naver``).
        """
        saved_rows = 0
        for row in rows:
            if not any(v is not None for v in (row.per, row.pbr, row.eps, row.bps)):
                continue
            saved_rows += 1
            existing = (
                self._db.query(SecurityFundamental)
                .filter(
                    SecurityFundamental.ticker == row.ticker,
                    SecurityFundamental.date == row.date,
                )
                .first()
            )
            if existing:
                existing.per = row.per
                existing.pbr = row.pbr
                existing.eps = row.eps
                existing.bps = row.bps
                existing.div = row.div
                existing.dps = row.dps
                existing.source = source
                existing.updated_at = datetime.datetime.now(datetime.timezone.utc)
            else:
                self._db.add(
                    SecurityFundamental(
                        ticker=row.ticker,
                        date=row.date,
                        per=row.per,
                        pbr=row.pbr,
                        eps=row.eps,
                        bps=row.bps,
                        div=row.div,
                        dps=row.dps,
                        source=source,
                    )
                )
        self._db.commit()
        return saved_rows

    def collect_fundamental_snapshot(
        self,
        adapter: "NaverFundamentalAdapter",
        ref_date: datetime.date,
        tickers: list[str],
    ) -> dict:
        """Naver 대체 소스로 ref_date 펀더멘털 스냅샷을 백필한다.

        KRX MDC 엔드포인트가 차단(``400 LOGOUT``)된 환경에서 pykrx 대신 사용한다.
        Naver는 현재 스냅샷만 제공하므로 단일 일자(ref_date)에만 적재된다.
        """
        rows = adapter.get_many(tickers, ref_date)
        saved = self._upsert_fundamentals(rows, source="naver")
        self._write_log(ref_date, "success", saved, source="naver.fundamental")
        return {
            "ref_date": ref_date.isoformat(),
            "requested": len(tickers),
            "fetched": len(rows),
            "rows": saved,
        }

    @staticmethod
    def _is_valid_ohlcv(row) -> bool:
        return (
            row.open is not None and row.open > 0
            and row.high is not None and row.high > 0
            and row.low is not None and row.low > 0
            and row.close is not None and row.close > 0
            and row.volume is not None and row.volume >= 0
        )

    def _write_log(
        self,
        ref_date: datetime.date,
        status: str,
        items: int,
        note: str | None = None,
        source: str = "krx",
    ) -> None:
        """collection_log 테이블에 감사 로그를 기록한다."""
        self._db.add(
            CollectionLog(
                ref_date=ref_date,
                source=source,
                status=status,
                items=items,
                note=note,
            )
        )
        self._db.commit()
