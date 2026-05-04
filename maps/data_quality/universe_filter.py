"""유니버스 필터 — as-of-date 기준 종목 생성기.

설계서 v2.6.3 §5 재설계.

핵심 원칙:
  - generate(ref_date, candidates) 형태의 기준일 관점 생성기.
  - ref_date 이후 정보(폐지, 정지)는 절대 참조하지 않는다.
  - backtest 모드: delisting_date > ref_date 이면 포함 (생존자 편향 방지).
  - live 모드: delisting_date <= ref_date 이면 즉시 제외.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field
from typing import Callable

from sqlalchemy.orm import Session

from maps.common.models import UniverseQualityLog
from maps.data.security_repo import Security

logger = logging.getLogger(__name__)

MIN_LISTING_DAYS = 100
MIN_TURNOVER_KRW: dict[str, float] = {
    "KOSPI": 5e8,
    "KOSDAQ": 3e8,
}
EXCLUDED_TYPES = {"SPAC"}
REJECTION_ALERT_THRESHOLD = 0.05  # 거부율 5% 초과 시 알림
MAX_MISSING_DAYS_20D = 1


@dataclass
class UniverseResult:
    """generate() 결과."""

    ref_date: datetime.date
    mode: str
    universe: list[Security] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)  # [{"ticker": ..., "reason": ...}]

    @property
    def rejection_ratio(self) -> float:
        total = len(self.universe) + len(self.rejected)
        return len(self.rejected) / total if total > 0 else 0.0


class DataQualityFilter:
    """as-of-date 유니버스 생성기 (v2.6.3 재설계).

    mode='backtest': WFA fold별 독립 유니버스 생성, 생존자 편향 제거.
    mode='live':     현재 실거래 가능 종목만 반환.
    """

    def __init__(
        self,
        db: Session,
        mode: str = "backtest",
        alert_fn: Callable[[str], None] | None = None,
    ) -> None:
        """
        Args:
            db: DB 세션 (universe_quality_log 기록용).
            mode: 'backtest' | 'live'
            alert_fn: 거부율 임계 초과 시 호출할 알림 함수.
        """
        if mode not in ("backtest", "live"):
            raise ValueError(f"mode는 'backtest' 또는 'live' 여야 합니다: {mode}")
        self._db = db
        self._mode = mode
        self._alert_fn = alert_fn or (lambda msg: logger.warning(msg))

    def generate(
        self, ref_date: datetime.date, candidates: list[Security]
    ) -> UniverseResult:
        """ref_date 기준 유니버스 스냅샷을 생성한다.

        Args:
            ref_date: 기준일. 이 날짜 이후의 사건은 반영하지 않는다.
            candidates: 전체 후보 종목 목록.

        Returns:
            UniverseResult.
        """
        result = UniverseResult(ref_date=ref_date, mode=self._mode)

        for stock in candidates:
            reason = self._check_as_of(stock, ref_date)
            if reason is None:
                result.universe.append(stock)
            else:
                result.rejected.append({"ticker": stock.ticker, "reason": reason})

        self._log(result)

        if result.rejection_ratio >= REJECTION_ALERT_THRESHOLD:
            self._alert_fn(
                f"[DQ] {ref_date} 거부율 {result.rejection_ratio:.1%} >= {REJECTION_ALERT_THRESHOLD:.0%}"
            )

        return result

    def _check_as_of(self, stock: Security, ref_date: datetime.date) -> str | None:
        """제외 사유를 반환한다. 정상이면 None.

        ref_date 이후 정보는 절대 참조하지 않는다.
        """
        # 0. 기본 메타데이터/가격 결측
        if not stock.ticker or not stock.name or not stock.market or not stock.security_type:
            return "missing_metadata"
        if not stock.has_live_ohlcv_as_of(ref_date):
            return "missing_price_data"
        if not stock.has_required_ohlcv_fields():
            return "missing_ohlcv_fields"
        if stock.has_excessive_missing_history(MAX_MISSING_DAYS_20D):
            return "missing_history"

        # 1. 수정주가
        if not stock.has_adjusted_price_as_of(ref_date):
            return "unadjusted_price"

        # 2. 상장 여부 (ref_date 기준 미상장)
        if stock.listing_date is not None and stock.listing_date > ref_date:
            return "not_listed_yet"

        # 3. 신규상장 (100일 미만)
        if stock.listing_days(ref_date) < MIN_LISTING_DAYS:
            return "recently_listed"

        # 4. 상장폐지
        if stock.delisting_date is not None:
            if self._mode == "live" and stock.delisting_date <= ref_date:
                return "delisted"
            if self._mode == "backtest" and stock.delisting_date < ref_date:
                # backtest: 폐지일 당일까지는 포함 (당일 종가로 청산 가능)
                return "delisted_before_ref"

        # 5. 거래정지 (ref_date 당일 정지 중)
        if stock.is_halted_on(ref_date):
            return "trading_halted"

        # 6. 관리종목
        if stock.is_managed_on(ref_date):
            return "managed_stock"

        # 7. 거래대금 (ref_date 기준 20일 평균)
        min_turnover = MIN_TURNOVER_KRW.get(stock.market, 5e8)
        if stock.avg_turnover_20d_as_of(ref_date) < min_turnover:
            return "low_turnover"

        # 8. 제외 유형 (SPAC 등)
        if stock.security_type in EXCLUDED_TYPES:
            return "excluded_type"

        return None

    def _log(self, result: UniverseResult) -> None:
        """universe_quality_log 테이블에 감사 로그를 기록한다."""
        try:
            self._db.add(
                UniverseQualityLog(
                    ref_date=result.ref_date,
                    mode=result.mode,
                    total_candidates=len(result.universe) + len(result.rejected),
                    kept_count=len(result.universe),
                    excluded_count=len(result.rejected),
                    rejection_ratio=result.rejection_ratio,
                    alert_sent=result.rejection_ratio >= REJECTION_ALERT_THRESHOLD,
                )
            )
            self._db.commit()
        except Exception as exc:
            logger.error("universe_quality_log 기록 실패: %s", exc)
            self._db.rollback()
