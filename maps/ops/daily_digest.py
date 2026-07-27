"""일일 다이제스트 — 하루치 매매 기록을 결정적으로 조립한다.

블로그 글은 2단계로 만들어진다.

    [1단계] 이 모듈이 DB에서 하루치 JSON(DailyDigest)을 만든다 — 수치·사유의 유일한 출처
    [2단계] 글쓰기 에이전트가 그 JSON만 읽고 Markdown을 쓴다 — DB 조회 권한 없음

객관성의 담보는 문체가 아니라 이 구조다. 다이제스트에 없는 숫자는 글에 쓸 수 없고,
실측되지 않은 팩터는 ``measured=False`` 로 내려보내 "미측정"임을 글에서 명시하게 한다.

새 계산 로직을 만들지 않고 기존 함수를 재사용하는 것이 원칙이다. 섹션 하나가 실패해도
나머지는 나와야 하므로 모든 섹션을 개별적으로 감싸고 사유를 ``errors`` 에 모은다.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from html.parser import HTMLParser

from sqlalchemy.orm import Session

from maps.api.schemas import (
    DailyDigest,
    DigestCandidate,
    DigestExecution,
    DigestFactor,
    DigestMarket,
    DigestReportExcerpt,
    DigestSector,
    DigestSectors,
    DigestStrategy,
)
from maps.common.models import (
    CandidateSnapshot,
    MarketRegimeLog,
    OrderLog,
    SecurityMetadata,
    StockReportRun,
)
from maps.common.settings import MapsSettings
from maps.execution.order_manager import kst_day_bounds_utc

logger = logging.getLogger(__name__)

# 후보 섹션에 담을 최대 종목 수. 글 한 편에서 다룰 수 있는 분량의 상한이다.
_MAX_CANDIDATES = 12
# stock_report 본문 발췌 상한(문자). 요약이 아니라 원문 앞부분을 그대로 자른다.
_MAX_EXCERPT_CHARS = 1500


class _TextExtractor(HTMLParser):
    """HTML에서 본문 텍스트만 뽑는다. 새 의존성 없이 표준 라이브러리만 쓴다."""

    _SKIP_TAGS = frozenset({"script", "style", "head"})

    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0 and data.strip():
            self._chunks.append(data.strip())

    @property
    def text(self) -> str:
        return re.sub(r"\s+", " ", " ".join(self._chunks)).strip()


def _html_to_text(html: str) -> str:
    """HTML 문자열을 공백 정규화된 평문으로 변환한다. 파싱 실패는 빈 문자열."""
    parser = _TextExtractor()
    try:
        parser.feed(html)
    except Exception:  # noqa: BLE001 — 깨진 HTML 때문에 다이제스트 전체가 죽으면 안 된다
        logger.warning("stock_report HTML 파싱 실패 — 발췌 생략")
        return ""
    return parser.text


def _build_market(db: Session, settings: MapsSettings, ref_date: dt.date) -> DigestMarket:
    """장세 섹션. 국면은 영속 로그가 정본, 팩터는 생성 시점에 재계산한다."""
    from maps.market.regime import PlaceholderKostolanyDataProvider, create_regime_analyzer

    row = (
        db.query(MarketRegimeLog)
        .filter(MarketRegimeLog.ref_date <= ref_date)
        .order_by(MarketRegimeLog.ref_date.desc())
        .first()
    )

    analyzer = create_regime_analyzer(settings)
    result = analyzer.analyze()
    composite = result.composite

    # 어떤 팩터가 실측인지는 MarketRegimeInput 구성부(regime.py의 _with_composite)가 결정한다.
    # 현재 base_input에 실려 나가는 것은 price_trend / volatility / foreign_fx 뿐이고,
    # liquidity·psychology는 매크로·수급·센티먼트 피드가 아직 연결되지 않아 중립값(50)이다.
    # 하드코딩 대신 provider 종류로 판정해, 실제 피드가 붙는 날 자동으로 True가 되게 한다.
    unmeasured_note = "매크로·수급·센티먼트 피드 미연결 — 중립값(50) 자리표시자"
    placeholder = type(getattr(analyzer, "_kostolany_provider", None)) is PlaceholderKostolanyDataProvider
    factors = [
        DigestFactor(name="price_trend", score=composite.price_trend_score if composite else None),
        DigestFactor(name="volatility", score=composite.volatility_score if composite else None),
        DigestFactor(name="foreign_fx", score=composite.foreign_fx_score if composite else None),
        DigestFactor(
            name="liquidity",
            score=composite.liquidity_score if composite else None,
            measured=not placeholder,
            note=None if not placeholder else unmeasured_note,
        ),
        DigestFactor(
            name="psychology",
            score=composite.psychology_score if composite else None,
            measured=not placeholder,
            note=None if not placeholder else unmeasured_note,
        ),
    ]

    return DigestMarket(
        regime=row.applied_regime if row else result.regime.value,
        raw_regime=row.raw_regime if row else None,
        weekly_trend=row.weekly_trend if row else result.weekly_trend.value,
        vol_regime=row.vol_regime if row else result.vol_regime.value,
        market_mode=result.market_mode(
            contrarian_enabled=settings.maps_contrarian_accumulation_enabled
        ).value,
        entry_limit_ratio=result.entry_limit_ratio,
        breadth_pct=row.breadth_pct if row else None,
        breadth_label=result.breadth.value,
        up_count=row.up_count if row else result.up_count,
        total_assets=row.total_assets if row else result.total_assets,
        kospi_above_ma5w=row.kospi_above_ma5w if row else result.kospi_above_ma5w,
        kospi_above_ma10w=row.kospi_above_ma10w if row else result.kospi_above_ma10w,
        kospi_ts=result.kospi_ts,
        floor_applied=bool(row.floor_applied) if row else result.floor_applied,
        final_market_score=composite.final_market_score if composite else None,
        factors=factors,
        reason=composite.reason if composite else None,
        source="market_regime_log" if row else "recomputed",
    )


def _placeholder_inputs(reason: str | None) -> list[str]:
    """스코어러가 스스로 표기한 중립 자리표시자 입력 목록을 뽑는다.

    SectorScorer 가 reason 끝에 ``; neutral placeholders=a,b,c`` 형태로 남긴다.
    스코어러가 권위이므로 여기서 다시 하드코딩하지 않는다 — 실데이터가 연결되는
    날 목록이 저절로 줄어든다. 형식이 바뀌면 빈 목록이 되지만 reason 원문을
    함께 내려보내므로 정보가 사라지지는 않는다.
    """
    marker = "neutral placeholders="
    if not reason or marker not in reason:
        return []
    return [part.strip() for part in reason.split(marker, 1)[1].split(",") if part.strip()]


def _build_sectors(db: Session, settings: MapsSettings, ref_date: dt.date) -> DigestSectors:
    """강세업종 섹션 — 매매 적용 여부와 무관하게 항상 관측한다.

    ``maps_sector_filter_enabled`` 는 "이 결과로 후보 유니버스를 자를지"만 정한다.
    꺼져 있어도 계산 자체는 DB만으로 결정적으로 되므로, 기록은 남긴다.
    선택기는 실제 매매 경로와 동일하게 고르므로 "켰다면 이렇게 됐을 것"이 정확하다.
    """
    from maps.market.regime import create_regime_analyzer
    from maps.market.sector_selector import SectorRegimeSelector, SectorSelector

    applied = settings.maps_sector_filter_enabled
    regime = create_regime_analyzer(settings).analyze()
    lookback = settings.maps_sector_lookback_days
    top_n = settings.maps_sector_top_n

    if settings.maps_sector_kostolany_mode_enabled:
        result = SectorRegimeSelector(lookback_days=lookback, top_n=top_n).select(
            db, ref_date, regime
        )
        chosen = set(result.selected_sectors)
        selected = [
            DigestSector(
                sector=name,
                score=score.score,
                momentum_20d=score.momentum_20d,
                momentum_60d=score.momentum_60d,
                turnover_growth=score.turnover_growth,
                reason=score.reason,
                overheat_warning=score.overheat_warning,
            )
            for name, score in result.sector_scores.items()
            if name in chosen
        ]
        placeholders = next(
            (p for p in (_placeholder_inputs(s.reason) for s in selected) if p), []
        )
        return DigestSectors(
            applied_to_trading=applied,
            selector="kostolany",
            selected=selected,
            watchlist=list(result.watchlist_sectors),
            overheated=list(result.overheated_sectors),
            excluded=dict(result.excluded_sectors),
            placeholder_inputs=placeholders,
            reason=result.reason,
        )

    # 레거시 선택기 — 자리표시자 없이 기간 수익률 순위만 쓴다(WEAK 장세는 방어업종 우선).
    base = SectorSelector(lookback_days=lookback, top_n=top_n)
    names = base.select_strong_sectors(db, ref_date, regime)
    returns = base._calc_sector_returns(  # noqa: SLF001 — 순위 근거 수치를 그대로 싣는다
        db, ref_date, ref_date - dt.timedelta(days=lookback * 2)
    )
    return DigestSectors(
        applied_to_trading=applied,
        selector="legacy",
        selected=[
            DigestSector(
                sector=name,
                momentum_20d=returns.get(name),
                reason="legacy: 기간 수익률 순위",
            )
            for name in names
        ],
        reason="기간 수익률 상위 N개" + ("" if applied else " (관측 전용 — 매매 미적용)"),
    )


def _build_strategies(
    db: Session, settings: MapsSettings, ref_date: dt.date
) -> list[DigestStrategy]:
    """오늘 어떤 전략이 왜 채택/차단됐는지. 스케줄러의 판정 로직을 그대로 재현한다."""
    from maps.market.regime import create_regime_analyzer
    from maps.ops.order_preview import _latest_promotions
    from maps.ops.scheduler import _RUNNABLE_STRATEGIES

    regime = create_regime_analyzer(settings).analyze()
    regime_label = regime.regime.value
    stages = _latest_promotions(db)

    out: list[DigestStrategy] = []
    for strategy_id, cls in _RUNNABLE_STRATEGIES.items():
        strategy_type = getattr(getattr(cls, "strategy_type", None), "value", None)
        preferred = [
            getattr(r, "value", str(r)) for r in getattr(cls, "preferred_regimes", [])
        ]
        block_reason: str | None = None
        limit_ratio: float | None = None
        if regime_label not in preferred:
            block_reason = f"preferred_regime_mismatch:{regime_label}"
        else:
            policy = regime.entry_policy_for_strategy(
                getattr(cls, "strategy_type", None),
                contrarian_enabled=settings.maps_contrarian_accumulation_enabled,
                contrarian_entry_limit_ratio=settings.maps_contrarian_max_entry_ratio,
            )
            limit_ratio = policy.entry_limit_ratio
            if not policy.allowed:
                block_reason = policy.reason
        out.append(
            DigestStrategy(
                strategy_id=strategy_id,
                strategy_type=strategy_type,
                stage=stages.get(strategy_id),
                preferred_regimes=preferred,
                active=block_reason is None,
                block_reason=block_reason,
                entry_limit_ratio=limit_ratio,
            )
        )
    return out


def _build_candidates(
    db: Session, ref_date: dt.date
) -> tuple[list[DigestCandidate], int, int]:
    """후보 종목 섹션. 상위 N건과 전체/제외 건수를 함께 반환한다."""
    rows = (
        db.query(CandidateSnapshot)
        .filter(CandidateSnapshot.ref_date == ref_date)
        .order_by(CandidateSnapshot.final_score.desc())
        .all()
    )
    excluded = sum(1 for r in rows if r.excluded_reason)
    top = [
        DigestCandidate(
            ticker=r.ticker,
            name=r.name,
            market=r.market,
            strategy_id=r.strategy_id,
            final_score=r.final_score,
            factor_score=r.factor_score,
            trend_strength=r.trend_strength,
            ts_bucket=r.ts_bucket,
            score_reason=r.score_reason,
            excluded_reason=r.excluded_reason,
            holding_type=r.holding_type,
            estimated_qty=r.estimated_qty,
            ai_technical_score=r.ai_technical_score,
            ai_analysis_memo=r.ai_analysis_memo,
            ai_buy_price=r.ai_buy_price,
            ai_stop_price=r.ai_stop_price,
            ai_target_price=r.ai_target_price,
            ai_contrarian_opinion=r.ai_contrarian_opinion,
            ai_contrarian_thesis=r.ai_contrarian_thesis,
            ai_contrarian_anti_thesis=r.ai_contrarian_anti_thesis,
            valuation_margin_score=r.valuation_margin_score,
            valuation_margin_reason=r.valuation_margin_reason,
        )
        for r in rows[:_MAX_CANDIDATES]
    ]
    return top, len(rows), excluded


def _build_executions(db: Session, ref_date: dt.date) -> list[DigestExecution]:
    """당일 체결·주문 내역. order_log 는 UTC 저장이라 KST 경계 변환이 필수다."""
    start, end = kst_day_bounds_utc(ref_date)
    rows = (
        db.query(OrderLog)
        .filter(OrderLog.created_at >= start, OrderLog.created_at < end)
        .order_by(OrderLog.id)
        .all()
    )
    if not rows:
        return []

    tickers = {r.ticker for r in rows}
    names = {
        m.ticker: m.name
        for m in db.query(SecurityMetadata).filter(SecurityMetadata.ticker.in_(tickers)).all()
    }
    # 매수의 "왜 샀나"는 같은 종목·전략의 최근 후보 스냅샷 score_reason 에서 가져온다.
    rationale: dict[tuple[str, str], str] = {}
    for snap in (
        db.query(CandidateSnapshot)
        .filter(CandidateSnapshot.ticker.in_(tickers), CandidateSnapshot.ref_date <= ref_date)
        .order_by(CandidateSnapshot.ref_date.desc())
        .all()
    ):
        key = (snap.ticker, snap.strategy_id)
        if key not in rationale and snap.score_reason:
            rationale[key] = snap.score_reason

    return [
        DigestExecution(
            order_id=r.order_id,
            strategy_id=r.strategy_id,
            ticker=r.ticker,
            name=names.get(r.ticker),
            side=r.side,
            qty=r.qty,
            order_price=r.order_price,
            fill_price=r.fill_price,
            fill_qty=r.fill_qty,
            status=r.status,
            mode=r.mode,
            exit_reason=r.exit_reason,
            created_at=r.created_at.isoformat(),
            entry_rationale=(
                rationale.get((r.ticker, r.strategy_id or "")) if r.side == "buy" else None
            ),
        )
        for r in rows
    ]


def _build_market_context(db: Session, ref_date: dt.date) -> list[DigestReportExcerpt]:
    """외부 stock_report 원문 발췌. 뉴스 대신 시장 맥락을 채우는 유일한 소스다."""
    start, end = kst_day_bounds_utc(ref_date)
    rows = (
        db.query(StockReportRun)
        .filter(
            StockReportRun.status == "completed",
            StockReportRun.created_at >= start,
            StockReportRun.created_at < end,
        )
        .order_by(StockReportRun.id)
        .all()
    )
    out: list[DigestReportExcerpt] = []
    for row in rows:
        text = _html_to_text(row.html_content or "")
        if not text:
            continue
        out.append(
            DigestReportExcerpt(
                report_type=row.report_type,
                trade_date=row.trade_date,
                excerpt=text[:_MAX_EXCERPT_CHARS],
            )
        )
    return out


def build_daily_digest(
    db: Session, settings: MapsSettings, ref_date: dt.date
) -> DailyDigest:
    """ref_date 하루치 다이제스트를 조립한다.

    섹션별 실패는 흡수하고 사유를 ``errors`` 에 담는다. 장세 분석은 외부 API
    (pykrx/yfinance)를 타므로 실패가 드물지 않은데, 그 때문에 나머지 섹션까지
    통째로 날아가면 그날 기록이 사라진다.
    """
    digest = DailyDigest(
        ref_date=ref_date.isoformat(),
        generated_at=dt.datetime.now(dt.timezone.utc).isoformat(),
    )

    def _section(name: str, fn):  # noqa: ANN001, ANN202 — 내부 헬퍼
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            logger.warning("다이제스트 섹션 실패 [%s]: %s", name, exc)
            digest.errors.append(f"{name}: {exc}")
            return None

    digest.market = _section("market", lambda: _build_market(db, settings, ref_date))
    digest.sectors = _section("sectors", lambda: _build_sectors(db, settings, ref_date))
    digest.strategies = _section(
        "strategies", lambda: _build_strategies(db, settings, ref_date)
    ) or []

    candidates = _section("candidates", lambda: _build_candidates(db, ref_date))
    if candidates is not None:
        digest.candidates, digest.candidate_total, digest.candidate_excluded = candidates

    def _preview():  # noqa: ANN202
        from maps.ops.order_preview import build_order_preview
        return build_order_preview(db, settings)

    digest.tomorrow_orders = _section("tomorrow_orders", _preview)
    digest.executions = _section("executions", lambda: _build_executions(db, ref_date)) or []
    digest.market_context = _section(
        "market_context", lambda: _build_market_context(db, ref_date)
    ) or []
    return digest
