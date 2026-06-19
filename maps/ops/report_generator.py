"""코스톨라니 스타일 일일 투자 판단 리포트 생성기.

기존 매수 후보 리스트 리포트를 코스톨라니 스타일 의사결정 리포트로 확장한다.

리포트 섹션:
1. 시장 상황 요약 (장세·유동성·심리·진입 한도)
2. 섹터 판단 (선택/제외/관찰 섹터)
3. 전략 실행 현황 (허용/차단 전략)
4. 최우선 종목 (holding_type·점수·가격 계획)
5. 포트폴리오 리스크 (현금·섹터·테마 비중)
6. 최종 실행 계획 (매수가능·관찰·매수금지 목록)
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class MarketSummary:
    """시장 상황 요약."""

    ref_date: datetime.date
    legacy_regime: str = "mixed"
    composite_regime: str | None = None
    market_mode: str = "NORMAL"
    vol_regime: str = "normal"
    liquidity_score: float | None = None
    psychology_score: float | None = None
    foreign_fx_score: float | None = None
    entry_limit_ratio: float = 0.5
    contrarian_entry_limit_ratio: float = 0.25
    conclusion: str = ""


@dataclass
class SectorSummary:
    """섹터 판단 요약."""

    selected_sectors: list[str] = field(default_factory=list)
    excluded_sectors: dict[str, str] = field(default_factory=dict)
    watchlist_sectors: list[str] = field(default_factory=list)
    overheated_sectors: list[str] = field(default_factory=list)
    sector_scores: dict[str, float] = field(default_factory=dict)


@dataclass
class StrategySummary:
    """전략 실행 현황 요약."""

    active_strategies: list[str] = field(default_factory=list)
    blocked_strategies: list[dict[str, str]] = field(default_factory=list)
    strategy_type_ratios: dict[str, float] = field(default_factory=dict)


@dataclass
class CandidateRow:
    """개별 종목 리포트 행."""

    ticker: str
    name: str
    holding_type: str | None
    strategy_name: str | None
    strategy_type: str | None
    final_score: float
    valuation_margin_score: float | None
    ai_contrarian_opinion: str | None
    ai_contrarian_score: float | None
    plan_buy: float | None
    technical_stop: float | None
    thesis_stop: float | None
    trading_target: float | None
    value_target: float | None
    position_size_pct: float | None
    excluded_reason: str | None
    ai_contrarian_reason: str | None


@dataclass
class PortfolioRisk:
    """포트폴리오 리스크 요약."""

    cash_ratio: float = 0.0
    min_cash_ratio_required: float = 0.25
    sector_exposures: dict[str, float] = field(default_factory=dict)
    theme_exposures: dict[str, float] = field(default_factory=dict)
    max_single_exposure: float = 0.0
    limit_breaches: list[str] = field(default_factory=list)


@dataclass
class DailyReport:
    """코스톨라니 스타일 일일 투자 판단 리포트."""

    ref_date: datetime.date
    market: MarketSummary | None = None
    sectors: SectorSummary | None = None
    strategies: StrategySummary | None = None
    buy_candidates: list[CandidateRow] = field(default_factory=list)
    watch_candidates: list[CandidateRow] = field(default_factory=list)
    excluded_candidates: list[CandidateRow] = field(default_factory=list)
    portfolio_risk: PortfolioRisk | None = None
    generated_at: datetime.datetime = field(default_factory=datetime.datetime.now)

    def to_text(self) -> str:
        """사람이 읽을 수 있는 텍스트 리포트를 생성한다."""
        lines: list[str] = []
        _h = lines.append

        _h("=" * 70)
        _h(f"[MAPS 코스톨라니 일일 투자 판단 리포트] {self.ref_date}")
        _h("=" * 70)

        # 1. 시장 상황
        if self.market:
            m = self.market
            _h("")
            _h("▶ 1. 시장 상황 요약")
            _h(f"  레거시 장세: {m.legacy_regime.upper()}"
               + (f" → 복합 장세: {m.composite_regime}" if m.composite_regime else ""))
            _h(f"  시장 모드: {m.market_mode} | 변동성: {m.vol_regime.upper()}")
            if m.liquidity_score is not None:
                _h(f"  유동성 점수: {m.liquidity_score:.1f}/100"
                   + (f" | 심리 점수: {m.psychology_score:.1f}/100" if m.psychology_score is not None else ""))
            _h(f"  진입 한도: {m.entry_limit_ratio:.0%}"
               + (f" | 역발상 한도: {m.contrarian_entry_limit_ratio:.0%}" if m.contrarian_entry_limit_ratio else ""))
            if m.conclusion:
                _h(f"  【오늘의 결론】 {m.conclusion}")

        # 2. 섹터 판단
        if self.sectors:
            s = self.sectors
            _h("")
            _h("▶ 2. 섹터 판단")
            _h(f"  선택 섹터: {', '.join(s.selected_sectors) or '없음'}")
            if s.excluded_sectors:
                _h(f"  제외 섹터: {', '.join(f'{k}({v})' for k, v in s.excluded_sectors.items())}")
            if s.watchlist_sectors:
                _h(f"  관찰 섹터: {', '.join(s.watchlist_sectors)}")
            if s.overheated_sectors:
                _h(f"  ⚠ 과열 경고: {', '.join(s.overheated_sectors)}")

        # 3. 전략 실행 현황
        if self.strategies:
            st = self.strategies
            _h("")
            _h("▶ 3. 전략 실행 현황")
            _h(f"  실행 전략: {', '.join(st.active_strategies) or '없음'}")
            if st.blocked_strategies:
                blocked_str = ", ".join(f"{b['strategy_id']}({b.get('reason', '')})" for b in st.blocked_strategies)
                _h(f"  차단 전략: {blocked_str}")

        # 4. 최우선 종목
        _h("")
        _h("▶ 4. 최우선 매수 후보")
        if self.buy_candidates:
            for c in self.buy_candidates:
                _h(f"  ◆ [{c.holding_type or '-'}] {c.ticker} {c.name} | {c.strategy_type or '-'}")
                _h(f"    최종점수: {c.final_score:.1f}"
                   + (f" | 가치마진: {c.valuation_margin_score:.1f}" if c.valuation_margin_score else "")
                   + (f" | AI의견: {c.ai_contrarian_opinion}" if c.ai_contrarian_opinion else ""))
                if c.plan_buy:
                    _h(f"    매수가: {c.plan_buy:,.0f}"
                       + (f" | 기술적손절: {c.technical_stop:,.0f}" if c.technical_stop else "")
                       + (f" | thesis손절: {c.thesis_stop:,.0f}" if c.thesis_stop else ""))
                if c.trading_target or c.value_target:
                    _h(f"    단기목표: {c.trading_target:,.0f}" if c.trading_target else "    단기목표: N/A", )
                    if c.value_target:
                        lines[-1] += f" | 장기목표(가치): {c.value_target:,.0f}"
        else:
            _h("  (매수 후보 없음)")

        # 5. 관찰 종목
        if self.watch_candidates:
            _h("")
            _h("▶ 5. 관찰 종목 (조건 미충족 — 매수 제외)")
            for c in self.watch_candidates[:5]:
                reason = c.excluded_reason or c.ai_contrarian_reason or "조건 미충족"
                _h(f"  ○ {c.ticker} {c.name} | 사유: {reason}")

        # 6. 포트폴리오 리스크
        if self.portfolio_risk:
            pr = self.portfolio_risk
            _h("")
            _h("▶ 6. 포트폴리오 리스크")
            _h(f"  현금 비중: {pr.cash_ratio:.1%} (최소 요구: {pr.min_cash_ratio_required:.1%})")
            if pr.sector_exposures:
                _h("  섹터별 비중: " + ", ".join(f"{k}={v:.1%}" for k, v in pr.sector_exposures.items()))
            if pr.limit_breaches:
                _h(f"  ⚠ 한도 초과: {', '.join(pr.limit_breaches)}")

        # 7. 제외 종목
        if self.excluded_candidates:
            _h("")
            _h("▶ 7. 제외 종목")
            for c in self.excluded_candidates[:5]:
                reason = c.excluded_reason or "-"
                _h(f"  ✕ {c.ticker} {c.name} | 사유: {reason}")

        _h("")
        _h(f"생성시각: {self.generated_at.strftime('%Y-%m-%d %H:%M:%S')}")
        _h("=" * 70)

        return "\n".join(lines)


class KostolanyReportGenerator:
    """코스톨라니 스타일 일일 리포트를 생성한다."""

    def generate(
        self,
        *,
        ref_date: datetime.date,
        regime_result: Any = None,
        sector_result: Any = None,
        strategy_context: dict | None = None,
        candidate_snapshots: list[Any] | None = None,
        portfolio_snapshot: Any = None,
        market_regime: str = "mixed",
    ) -> DailyReport:
        """일일 리포트를 생성한다.

        Args:
            ref_date: 기준일
            regime_result: RegimeResult 객체 (market/regime.py)
            sector_result: SectorSelectionResult 객체 (market/sector_selector.py)
            strategy_context: 전략 실행 컨텍스트 dict
            candidate_snapshots: CandidateSnapshot ORM 객체 목록
            portfolio_snapshot: PortfolioSnapshot ORM 객체
            market_regime: 현재 장세 문자열
        """
        market = self._build_market_summary(ref_date, regime_result, market_regime)
        sectors = self._build_sector_summary(sector_result)
        strategies = self._build_strategy_summary(strategy_context)
        buy_cands, watch_cands, excluded_cands = self._split_candidates(candidate_snapshots or [])
        portfolio_risk = self._build_portfolio_risk(portfolio_snapshot, market_regime)

        return DailyReport(
            ref_date=ref_date,
            market=market,
            sectors=sectors,
            strategies=strategies,
            buy_candidates=buy_cands,
            watch_candidates=watch_cands,
            excluded_candidates=excluded_cands,
            portfolio_risk=portfolio_risk,
        )

    def _build_market_summary(
        self, ref_date: datetime.date, regime_result: Any, market_regime: str
    ) -> MarketSummary:
        summary = MarketSummary(ref_date=ref_date, legacy_regime=market_regime)
        if regime_result is None:
            return summary

        summary.legacy_regime = getattr(regime_result, "regime", market_regime)
        if hasattr(summary.legacy_regime, "value"):
            summary.legacy_regime = summary.legacy_regime.value

        composite = getattr(regime_result, "composite", None)
        if composite:
            summary.composite_regime = getattr(composite, "composite_regime", None)
            summary.liquidity_score = getattr(composite, "liquidity_score", None)
            summary.psychology_score = getattr(composite, "psychology_score", None)
            summary.foreign_fx_score = getattr(composite, "foreign_fx_score", None)

        vol = getattr(regime_result, "vol_regime", None)
        if vol:
            summary.vol_regime = vol.value if hasattr(vol, "value") else str(vol)

        summary.entry_limit_ratio = getattr(regime_result, "entry_limit_ratio", 0.5)
        summary.conclusion = self._make_conclusion(summary)
        return summary

    @staticmethod
    def _make_conclusion(m: MarketSummary) -> str:
        regime = (m.composite_regime or m.legacy_regime).lower()
        vol = m.vol_regime.lower()
        if regime == "weak" and vol == "high":
            if m.contrarian_entry_limit_ratio > 0:
                return ("현재 시장은 공포 구간입니다. 유동성이 개선 중이라면 "
                        "역발상 우량주 분할 매수 후보만 선택적으로 허용합니다.")
            return "현재 시장은 공포·고변동성 구간입니다. 신규 매수를 전면 보류하고 현금을 확보합니다."
        if regime in ("weak", "contrarian"):
            return "약세장 구간입니다. 방어 섹터와 가치 안전마진이 높은 역발상 후보를 우선합니다."
        if regime == "mixed":
            return "혼조 구간입니다. 눌림목 전략과 이익 수정 상향 섹터 중심으로 선별합니다."
        return "강세장 구간입니다. 추세·돌파 전략 중심으로 비중을 확대합니다."

    @staticmethod
    def _build_sector_summary(sector_result: Any) -> SectorSummary | None:
        if sector_result is None:
            return None
        return SectorSummary(
            selected_sectors=list(getattr(sector_result, "selected_sectors", [])),
            excluded_sectors=dict(getattr(sector_result, "excluded_sectors", {})),
            watchlist_sectors=list(getattr(sector_result, "watchlist_sectors", [])),
            overheated_sectors=list(getattr(sector_result, "overheated_sectors", [])),
        )

    @staticmethod
    def _build_strategy_summary(strategy_context: dict | None) -> StrategySummary | None:
        if strategy_context is None:
            return None
        return StrategySummary(
            active_strategies=strategy_context.get("strategies_updated", []),
            blocked_strategies=strategy_context.get("strategies_blocked", []),
        )

    @staticmethod
    def _split_candidates(
        snapshots: list[Any],
    ) -> tuple[list[CandidateRow], list[CandidateRow], list[CandidateRow]]:
        buy_cands: list[CandidateRow] = []
        watch_cands: list[CandidateRow] = []
        excluded_cands: list[CandidateRow] = []

        for snap in snapshots:
            holding = getattr(snap, "holding_type", None)
            excluded = getattr(snap, "excluded_reason", None)
            ai_opinion = getattr(snap, "ai_contrarian_opinion", None)

            row = CandidateRow(
                ticker=snap.ticker,
                name=snap.name,
                holding_type=holding,
                strategy_name=snap.strategy_id,
                strategy_type=getattr(snap, "strategy_type", None),
                final_score=snap.final_score,
                valuation_margin_score=getattr(snap, "valuation_margin_score", None),
                ai_contrarian_opinion=ai_opinion,
                ai_contrarian_score=getattr(snap, "ai_contrarian_score", None),
                plan_buy=getattr(snap, "ai_buy_price", None),
                technical_stop=getattr(snap, "technical_stop", None),
                thesis_stop=getattr(snap, "thesis_stop", None),
                trading_target=getattr(snap, "trading_target", None),
                value_target=getattr(snap, "value_target", None),
                position_size_pct=None,
                excluded_reason=excluded,
                ai_contrarian_reason=getattr(snap, "ai_contrarian_reason", None),
            )

            if holding in ("BAN",) or ai_opinion == "REJECT":
                excluded_cands.append(row)
            elif holding == "WATCH" or not snap.weekly_pass:
                watch_cands.append(row)
            elif excluded:
                excluded_cands.append(row)
            else:
                buy_cands.append(row)

        # final_score 내림차순 정렬
        buy_cands.sort(key=lambda r: r.final_score, reverse=True)
        return buy_cands, watch_cands, excluded_cands

    @staticmethod
    def _build_portfolio_risk(portfolio_snapshot: Any, market_regime: str) -> PortfolioRisk | None:
        if portfolio_snapshot is None:
            return None
        min_ratios = {"strong": 0.15, "mixed": 0.25, "weak": 0.35}
        return PortfolioRisk(
            cash_ratio=getattr(portfolio_snapshot, "cash_ratio", 0.0),
            min_cash_ratio_required=min_ratios.get(market_regime.lower(), 0.25),
        )
