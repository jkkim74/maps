"""장세 판정 이력 기반 히스테리시스 — buffer band·전일 유지·floor 2일 확인.

8개 자산의 5주MA 상회 비율(up_ratio)은 이산적(0/8~8/8)이어서 임계값(40%) 근처
(3/8=37.5%)에서 자산 1개 등락만으로 국면이 매일 뒤집힌다. 이를 막기 위해:

- up_ratio ≤ 35% → weak 확정
- up_ratio ≥ 45% → raw 판정 그대로 (mixed/strong)
- 35% < up_ratio < 45% (8자산 기준 3/8) → 직전 applied_regime 유지

또한 KOSPI 하한선(floor)의 "2일 연속 MA5W 위" 확인을 전일 이력으로 수행하고,
Korea weak guard(MIXED인데 KOSPI 5·10주선 하회 + 추세강도/breadth 약세 → WEAK 하향,
플로어의 대칭)를 적용한다. 판정 결과는 market_regime_log에 upsert되어 다음 날
판정의 근거가 된다.

참고: raw_regime 컬럼은 stateless floor 적용 **후** 값이다 (floor_applied로 구분).
"""

from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy.orm import Session

from maps.common.models import MarketRegimeLog
from maps.common.settings import MapsSettings, get_settings
from maps.market.regime import (
    BreadthLabel,
    RegimeLabel,
    RegimeResult,
    WeeklyTrendLabel,
    korea_weak_guard_triggered,
)

logger = logging.getLogger(__name__)

# buffer band 경계 (up_ratio 기준)
_WEAK_ENTER_RATIO = 0.35   # 이하 → weak 확정
_MIXED_ENTER_RATIO = 0.45  # 이상 → raw 판정 그대로


def _previous_log(db: Session, ref_date: dt.date) -> MarketRegimeLog | None:
    """ref_date 이전의 가장 최근 판정 이력을 반환한다."""
    return (
        db.query(MarketRegimeLog)
        .filter(MarketRegimeLog.ref_date < ref_date)
        .order_by(MarketRegimeLog.ref_date.desc())
        .first()
    )


def latest_applied_regime(db: Session, ref_date: dt.date, max_age_days: int = 3) -> MarketRegimeLog | None:
    """ref_date 기준 최근(max_age_days 이내) 판정 이력을 반환한다 (읽기 전용).

    주문 미리보기 등 판정을 새로 쓰지 않는 경로에서 사용한다.
    """
    cutoff = ref_date - dt.timedelta(days=max_age_days)
    return (
        db.query(MarketRegimeLog)
        .filter(MarketRegimeLog.ref_date <= ref_date, MarketRegimeLog.ref_date >= cutoff)
        .order_by(MarketRegimeLog.ref_date.desc())
        .first()
    )


def apply_hysteresis(
    db: Session,
    result: RegimeResult,
    ref_date: dt.date,
    *,
    source: str = "scheduler",
    breadth_pct: float | None = None,
    settings: MapsSettings | None = None,
) -> RegimeResult:
    """raw 장세 판정에 히스테리시스와 Korea weak guard를 적용하고 이력을 upsert한다.

    result를 제자리 수정해 반환한다 (breadth 등 주입된 필드 보존).
    오버라이드/스텁 결과(up_count=None)는 판정을 바꾸지 않고 기록만 한다.
    """
    settings = settings or get_settings()
    raw_regime = result.regime
    prev = _previous_log(db, ref_date)

    if result.up_count is not None and result.total_assets:
        up_ratio = result.up_count / result.total_assets

        # breadth 미주입 시 여기서 계산한다 — 주문 사이클처럼 breadth를 계산하지
        # 않는 호출자에서도 breadth 기반 가드들이 실제로 동작하게 하는 단일 지점.
        if result.breadth == BreadthLabel.UNKNOWN and settings.maps_breadth_guard_enabled:
            from maps.market.breadth import classify_breadth, compute_pct_above_ma

            if breadth_pct is None:
                breadth_pct = compute_pct_above_ma(
                    db, ref_date, ma_window=settings.maps_breadth_ma_window
                )
            result.breadth = classify_breadth(
                breadth_pct, weak_threshold=settings.maps_breadth_weak_threshold
            )

        # floor 2일 확인: MA10W 아래라 stateless floor가 발동하지 않았지만
        # 전일에 이어 2일 연속 KOSPI가 MA5W 위면 하한선을 적용한다.
        if (
            result.regime == RegimeLabel.WEAK
            and not result.floor_applied
            and result.kospi_above_ma5w
            and result.weekly_trend == WeeklyTrendLabel.PASS
            and prev is not None
            and prev.kospi_above_ma5w
        ):
            logger.info(
                "KOSPI MA5W 2일 연속 상회 + weekly_trend pass → 국면 하한선 적용 (weak→mixed)"
            )
            result.regime = RegimeLabel.MIXED
            result.floor_applied = True

        # buffer band: 경계 구간(3/8 등)에서는 직전 판정을 유지한다.
        elif (
            _WEAK_ENTER_RATIO < up_ratio < _MIXED_ENTER_RATIO
            and prev is not None
            and prev.applied_regime != result.regime.value
        ):
            try:
                held = RegimeLabel(prev.applied_regime)
            except ValueError:
                held = None
            if held is not None:
                logger.info(
                    "장세 buffer band (up_ratio=%.3f) → 직전 판정 유지 (%s→%s)",
                    up_ratio,
                    result.regime.value,
                    held.value,
                )
                result.regime = held

        # Korea weak guard: 글로벌 투표가 MIXED여도 KOSPI가 5·10주선을 모두 깨고
        # 추세강도/breadth가 약하면 WEAK로 내린다. 플로어(상회 필요)와 상호배타이고,
        # buffer band가 유지한 MIXED보다 우선한다 — 한국 실측이 부정하는 라벨을
        # 관성으로 유지하면 가드의 목적이 무너진다. 그래서 band 뒤에 둔다.
        if settings.maps_korea_weak_guard_enabled and korea_weak_guard_triggered(
            result, ts_threshold=settings.maps_korea_weak_ts_threshold
        ):
            logger.info(
                "Korea weak guard: KOSPI 5·10주선 하회 + ts=%s breadth=%s → mixed→weak",
                result.kospi_ts,
                result.breadth.value,
            )
            result.regime = RegimeLabel.WEAK
            result.korea_weak_applied = True

    _upsert_log(db, result, raw_regime, ref_date, source=source, breadth_pct=breadth_pct)
    return result


def _upsert_log(
    db: Session,
    result: RegimeResult,
    raw_regime: RegimeLabel,
    ref_date: dt.date,
    *,
    source: str,
    breadth_pct: float | None = None,
) -> None:
    """판정 결과를 market_regime_log에 upsert한다."""
    row = (
        db.query(MarketRegimeLog)
        .filter(MarketRegimeLog.ref_date == ref_date)
        .first()
    )
    if row is None:
        row = MarketRegimeLog(ref_date=ref_date)
        db.add(row)
    row.raw_regime = raw_regime.value
    row.applied_regime = result.regime.value
    row.up_count = result.up_count
    row.total_assets = result.total_assets
    row.weekly_trend = result.weekly_trend.value
    row.vol_regime = result.vol_regime.value
    row.floor_applied = result.floor_applied
    row.korea_weak_guard_applied = result.korea_weak_applied
    if breadth_pct is not None:
        row.breadth_pct = breadth_pct
    row.kospi_above_ma5w = result.kospi_above_ma5w
    row.kospi_above_ma10w = result.kospi_above_ma10w
    row.source = source
    db.commit()
