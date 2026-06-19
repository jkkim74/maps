"""업종 강도 기반 필터 — 최근 N거래일 수익률 상위 업종 선정.

WEAK 시황에서는 방어 업종(유틸리티·헬스케어·필수소비재)을 우선 선택한다.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass
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


_KOSTOLANY_DEFENSIVE_SECTORS: frozenset[str] = frozenset({
    "유틸리티",
    "헬스케어",
    "필수소비재",
    "에너지",
    *_DEFENSIVE_SECTORS,
})


@dataclass(frozen=True)
class SectorScore:
    sector: str
    score: float
    momentum_20d: float
    momentum_60d: float
    earnings_revision: float
    flow_improvement: float
    valuation_attractiveness: float
    overheat_inverse: float
    macro_fit: float
    turnover_growth: float
    reason: str
    overheat_warning: str | None = None


@dataclass(frozen=True)
class SectorSelectionResult:
    selected_sectors: list[str]
    excluded_sectors: dict[str, str]
    watchlist_sectors: list[str]
    sector_scores: dict[str, SectorScore]
    overheated_sectors: list[str]
    reason: str


class SectorScorer:
    """Kostolany-style sector cycle scorer with neutral placeholders."""

    _WEIGHTS = {
        "momentum_20d": 0.20,
        "momentum_60d": 0.15,
        "earnings_revision": 0.25,
        "flow_improvement": 0.15,
        "valuation_attractiveness": 0.10,
        "overheat_inverse": 0.10,
        "macro_fit": 0.05,
    }

    def score(
        self,
        *,
        sector: str,
        momentum_20d: float | None = None,
        momentum_60d: float | None = None,
        earnings_revision: float | None = None,
        flow_improvement: float | None = None,
        valuation_attractiveness: float | None = None,
        overheat: float | None = None,
        macro_fit: float | None = None,
        turnover_growth: float | None = None,
    ) -> SectorScore:
        m20 = _return_to_score(momentum_20d)
        m60 = _return_to_score(momentum_60d)
        earnings = _score_or_neutral(earnings_revision)
        flow = _score_or_neutral(flow_improvement if flow_improvement is not None else turnover_growth)
        valuation = _score_or_neutral(valuation_attractiveness)
        overheat_value = _score_or_neutral(overheat)
        overheat_inverse = round(100.0 - overheat_value, 2)
        macro = _score_or_neutral(macro_fit)
        components = {
            "momentum_20d": m20,
            "momentum_60d": m60,
            "earnings_revision": earnings,
            "flow_improvement": flow,
            "valuation_attractiveness": valuation,
            "overheat_inverse": overheat_inverse,
            "macro_fit": macro,
        }
        score = round(sum(components[key] * weight for key, weight in self._WEIGHTS.items()), 2)
        missing = [
            name for name, value in {
                "earnings_revision": earnings_revision,
                "flow_improvement": flow_improvement,
                "valuation_attractiveness": valuation_attractiveness,
                "overheat": overheat,
                "macro_fit": macro_fit,
            }.items()
            if value is None
        ]
        reason = (
            f"score={score:.1f}, m20={m20:.1f}, m60={m60:.1f}, "
            f"earnings={earnings:.1f}, flow={flow:.1f}, valuation={valuation:.1f}"
        )
        if missing:
            reason = f"{reason}; neutral placeholders={','.join(missing)}"
        return SectorScore(
            sector=sector,
            score=score,
            momentum_20d=m20,
            momentum_60d=m60,
            earnings_revision=earnings,
            flow_improvement=flow,
            valuation_attractiveness=valuation,
            overheat_inverse=overheat_inverse,
            macro_fit=macro,
            turnover_growth=_score_or_neutral(turnover_growth),
            reason=reason,
            overheat_warning="overheated" if overheat_value >= 75.0 else None,
        )


class SectorRegimeSelector:
    """Select, exclude, and watch sectors by market regime."""

    def __init__(self, lookback_days: int = 20, top_n: int = 5, scorer: SectorScorer | None = None) -> None:
        self._lookback_days = lookback_days
        self._top_n = top_n
        self._scorer = scorer or SectorScorer()

    def select(
        self,
        db: "Session",
        ref_date: datetime.date,
        regime: "RegimeResult",
    ) -> SectorSelectionResult:
        scores = self._score_from_db(db, ref_date)
        if not scores:
            return SectorSelectionResult([], {}, [], {}, [], "sector data unavailable: filter not applied")
        return self.select_from_scores(scores, regime)

    def select_from_scores(
        self,
        scores: list[SectorScore],
        regime: "RegimeResult",
    ) -> SectorSelectionResult:
        from maps.market.regime import RegimeLabel, VolRegimeLabel

        ranked = sorted(scores, key=lambda item: item.score, reverse=True)
        score_by_sector = {item.sector: item for item in ranked}
        excluded: dict[str, str] = {}
        watchlist: list[str] = []
        overheated = [item.sector for item in ranked if item.overheat_warning]
        liquidity_improving = _liquidity_improving(regime)

        if regime.regime == RegimeLabel.STRONG:
            selected = [
                item.sector for item in ranked
                if item.momentum_20d >= 50.0 and item.momentum_60d >= 50.0 and item.turnover_growth >= 50.0
            ][: self._top_n]
            reason = "strong: leading momentum sectors with turnover confirmation"
        elif regime.regime == RegimeLabel.MIXED:
            eligible = [
                item for item in ranked
                if item.sector not in overheated and item.earnings_revision >= 50.0
            ]
            selected = [item.sector for item in eligible][: self._top_n]
            for item in ranked:
                if item.sector in overheated:
                    excluded[item.sector] = "mixed_overheated_sector_excluded"
            reason = "mixed: momentum plus earnings revision, overheated sectors excluded"
        elif regime.regime == RegimeLabel.WEAK and regime.vol_regime == VolRegimeLabel.HIGH:
            defensive = [item for item in ranked if _is_defensive(item.sector)]
            selected = [item.sector for item in defensive][: self._top_n]
            for item in ranked:
                if item.sector not in selected:
                    excluded[item.sector] = "weak_high_blocks_new_momentum_sector"
            if liquidity_improving:
                watchlist = [
                    item.sector for item in ranked
                    if item.sector not in selected and item.score >= 55.0 and item.overheat_warning is None
                ][: self._top_n]
            reason = "weak_high: defensive sectors only; momentum sectors blocked"
        else:
            defensive = [item for item in ranked if _is_defensive(item.sector)]
            others = [item for item in ranked if not _is_defensive(item.sector)]
            selected = [item.sector for item in (defensive + others)][: self._top_n]
            if liquidity_improving:
                watchlist = [
                    item.sector for item in others
                    if item.score >= 55.0 and item.overheat_warning is None
                ][: self._top_n]
            reason = "weak: defensive sectors first; liquidity improvement adds next leaders to watchlist"

        if not selected:
            selected = [item.sector for item in ranked[: self._top_n]]
            reason = f"{reason}; fallback to top scored sectors"

        return SectorSelectionResult(
            selected_sectors=selected,
            excluded_sectors=excluded,
            watchlist_sectors=watchlist,
            sector_scores=score_by_sector,
            overheated_sectors=overheated,
            reason=reason,
        )

    def _score_from_db(self, db: "Session", ref_date: datetime.date) -> list[SectorScore]:
        cutoff_20 = ref_date - datetime.timedelta(days=self._lookback_days * 2)
        cutoff_60 = ref_date - datetime.timedelta(days=120)
        base_selector = SectorSelector(self._lookback_days, self._top_n)
        returns_20 = base_selector._calc_sector_returns(db, ref_date, cutoff_20)
        returns_60 = base_selector._calc_sector_returns(db, ref_date, cutoff_60)
        turnover_growth = self._calc_turnover_growth(db, ref_date)
        sectors = sorted(set(returns_20) | set(returns_60) | set(turnover_growth))
        return [
            self._scorer.score(
                sector=sector,
                momentum_20d=returns_20.get(sector),
                momentum_60d=returns_60.get(sector),
                flow_improvement=turnover_growth.get(sector),
                turnover_growth=turnover_growth.get(sector),
            )
            for sector in sectors
        ]

    def _calc_turnover_growth(self, db: "Session", ref_date: datetime.date) -> dict[str, float]:
        cutoff = ref_date - datetime.timedelta(days=self._lookback_days * 2)
        rows = (
            db.query(
                SecurityMetadata.sector,
                func.avg(HistoricalOHLCV.close * HistoricalOHLCV.volume).label("avg_turnover"),
            )
            .join(SecurityMetadata, HistoricalOHLCV.ticker == SecurityMetadata.ticker)
            .filter(
                SecurityMetadata.sector.isnot(None),
                HistoricalOHLCV.date >= cutoff,
                HistoricalOHLCV.date <= ref_date,
            )
            .group_by(SecurityMetadata.sector)
            .having(func.count(SecurityMetadata.ticker.distinct()) >= 3)
            .all()
        )
        values = [float(row.avg_turnover) for row in rows if row.avg_turnover is not None]
        max_value = max(values, default=0.0)
        if max_value <= 0.0:
            return {}
        return {
            row.sector: round(float(row.avg_turnover) / max_value * 100.0, 2)
            for row in rows
            if row.avg_turnover is not None
        }


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


def _score_or_neutral(value: float | int | None, default: float = 50.0) -> float:
    if value is None:
        return default
    try:
        return round(max(0.0, min(float(value), 100.0)), 2)
    except (TypeError, ValueError):
        return default


def _return_to_score(value: float | int | None) -> float:
    if value is None:
        return 50.0
    try:
        pct = float(value)
    except (TypeError, ValueError):
        return 50.0
    # -20% maps near 0, 0% maps 50, +20% maps near 100.
    return round(max(0.0, min(50.0 + pct * 250.0, 100.0)), 2)


def _is_defensive(sector: str) -> bool:
    return sector in _KOSTOLANY_DEFENSIVE_SECTORS


def _liquidity_improving(regime: "RegimeResult") -> bool:
    composite = getattr(regime, "composite", None)
    if composite is None:
        return False
    return float(getattr(composite, "liquidity_score", 0.0) or 0.0) >= 60.0
